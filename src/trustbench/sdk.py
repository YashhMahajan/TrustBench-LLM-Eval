"""Send production traces to TrustBench from your own Python code.

    from trustbench.sdk import TrustBench
    tb = TrustBench()                      # reads TRUSTBENCH_API_KEY and TRUSTBENCH_URL

    with tb.trace("support-bot", model="gpt-4o-mini") as t:
        t.input = question
        with t.span("search", kind="retrieval"):
            docs = search(question)
        t.output = call_model(question, docs)

Safety rules of this library:
  - It never raises an error into your code. If the server is down, it drops the traces and counts them.
  - It never slows your code. A background thread sends the traces in batches.
  - It removes personal data and secrets in your process, before it sends anything (redact=True).
  - Without an API key it does nothing.
"""

import atexit
import functools
import inspect
import logging
import os
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

from .redact import redact as _redact

log = logging.getLogger("trustbench")
DEFAULT_URL = "http://127.0.0.1:8000"


@dataclass
class Stats:
    sent: int = 0          # Accepted by the server.
    duplicates: int = 0
    rejected: int = 0      # Refused by the server as invalid.
    dropped: int = 0       # Lost: the queue was full, or the server did not answer.


class Span:
    def __init__(self, name: str, kind: str, offset_ms: float, parent_id: str | None, attrs: dict):
        self.span_id, self.name, self.kind, self.offset_ms, self.parent_id = uuid.uuid4().hex[:16], name, kind, offset_ms, parent_id
        self.attrs, self.status, self.duration_ms = dict(attrs), "ok", 0.0

    def set(self, **attrs) -> None:
        self.attrs.update(attrs)


class Trace:
    FIELDS = ("name", "model", "prompt_version", "input", "output", "context", "input_tokens", "output_tokens", "cost_usd", "tags", "error_type", "status")

    def __init__(self, client: "TrustBench", name: str, **fields):
        self._client, self._start = client, time.perf_counter()
        self.trace_id = fields.pop("trace_id", None) or uuid.uuid4().hex
        self.name = name
        self.model = self.prompt_version = self.input = self.output = self.context = None
        self.input_tokens = self.output_tokens = self.cost_usd = self.error_type = None
        self.status, self.tags = "ok", {}
        self._started_at = time.time()
        self.spans: list[Span] = []
        self._stack: list[Span] = []
        self.set(**fields)

    def set(self, **fields) -> "Trace":
        for k, v in fields.items():
            if k not in self.FIELDS:
                raise TypeError(f"Unknown trace field '{k}'.")
            setattr(self, k, v)
        return self

    @contextmanager
    def span(self, name: str, kind: str = "other", **attrs):
        sp = Span(name, kind, (time.perf_counter() - self._start) * 1000, self._stack[-1].span_id if self._stack else None, attrs)
        self.spans.append(sp)
        self._stack.append(sp)
        t0 = time.perf_counter()
        try:
            yield sp
        except BaseException:
            sp.status = "error"
            raise
        finally:
            sp.duration_ms = (time.perf_counter() - t0) * 1000
            self._stack.pop()

    def _payload(self, redact: bool) -> dict:
        clean = (lambda x: _redact(x)[0] if redact and isinstance(x, str) else x)
        scrub = lambda d: {k: clean(v) for k, v in d.items()}
        return {
            "trace_id": self.trace_id, "name": self.name, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(self._started_at)) + f".{int(self._started_at % 1 * 1e6):06d}Z",
            "duration_ms": (time.perf_counter() - self._start) * 1000, "status": self.status, "error_type": self.error_type, "model": self.model,
            "prompt_version": self.prompt_version, "input": clean(self.input), "output": clean(self.output), "context": clean(self.context),
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "cost_usd": self.cost_usd, "tags": scrub(self.tags),
            "spans": [{"span_id": s.span_id, "parent_id": s.parent_id, "kind": s.kind, "name": s.name, "offset_ms": s.offset_ms,
                       "duration_ms": s.duration_ms, "status": s.status, "attrs": scrub(s.attrs)} for s in self.spans],
        }


class TrustBench:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, name: str = "default", redact: bool = True,
                 batch_size: int = 50, flush_interval: float = 2.0, max_queue: int = 10000, timeout: float = 5.0,
                 retries: int = 3, client: httpx.Client | None = None):
        self.api_key = api_key or os.environ.get("TRUSTBENCH_API_KEY")
        self.base_url = (base_url or os.environ.get("TRUSTBENCH_URL") or DEFAULT_URL).rstrip("/")
        self.name, self.redact, self.batch_size, self.retries = name, redact, batch_size, retries
        self.flush_interval, self.stats = flush_interval, Stats()
        self.enabled = bool(self.api_key)
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._http = client or httpx.Client(timeout=timeout)
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread: threading.Thread | None = None
        if not self.enabled:
            log.warning("TrustBench: no API key (TRUSTBENCH_API_KEY). Tracing is switched off.")
        else:
            self._thread = threading.Thread(target=self._loop, name="trustbench-sender", daemon=True)
            self._thread.start()
            atexit.register(self.close)

    # ---- Writing traces ---------------------------------------------------------------

    @contextmanager
    def trace(self, name: str | None = None, **fields):
        t = Trace(self, name or self.name, **fields)
        try:
            yield t
        except BaseException as e:
            t.status, t.error_type = "error", type(e).__name__
            raise                       # Your own error always reaches your own code.
        finally:
            self._submit(t)

    def log(self, name: str | None = None, **fields) -> str:
        """Send one finished trace. Returns its id."""
        t = Trace(self, name or self.name, **fields)
        self._submit(t)
        return t.trace_id

    def observe(self, name: str | None = None, model: str | None = None, capture_input: bool = False, capture_output: bool = False):
        """Decorator. Records time and errors of a function. The SDK does NOT record arguments and results unless you ask."""
        def wrap(fn):
            label = name or fn.__name__

            def done(t, args, kwargs, result=None):
                if capture_input:
                    t.input = repr((args, kwargs))
                if capture_output:
                    t.output = result if isinstance(result, str) else repr(result)

            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def awrapper(*args, **kwargs):
                    with self.trace(label, model=model) as t:
                        result = await fn(*args, **kwargs)
                        done(t, args, kwargs, result)
                        return result
                return awrapper

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                with self.trace(label, model=model) as t:
                    result = fn(*args, **kwargs)
                    done(t, args, kwargs, result)
                    return result
            return wrapper
        return wrap

    def feedback(self, trace_id: str, value: int) -> None:
        """Say what a user thought of an answer: 1 good, -1 bad. Sent after the trace itself."""
        if self.enabled and value in (-1, 0, 1):
            self._enqueue(("feedback", trace_id, value))

    # ---- Sending ----------------------------------------------------------------------

    def _submit(self, t: Trace) -> None:
        if not self.enabled:
            return
        try:
            self._enqueue(("trace", t._payload(self.redact)))
        except Exception:   # Building the payload must never break the application.
            self.stats.dropped += 1
            log.debug("TrustBench: could not build a trace", exc_info=True)

    def _enqueue(self, item) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self.stats.dropped += 1

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.flush_interval)
            self.flush()

    def flush(self) -> None:
        """Send everything that waits. Returns when the queue is empty or the server cannot be reached."""
        with self._lock:
            while self.enabled:
                items = []
                while len(items) < self.batch_size:
                    try:
                        items.append(self._q.get_nowait())
                    except queue.Empty:
                        break
                if not items:
                    return
                traces = [i[1] for i in items if i[0] == "trace"]
                if traces:
                    self._send_traces(traces)
                for _, tid, value in (i for i in items if i[0] == "feedback"):
                    self._request("POST", f"/v1/traces/{tid}/feedback", {"value": value})

    def _request(self, method: str, path: str, body: dict) -> httpx.Response | None:
        delay = 0.2
        for attempt in range(self.retries):
            try:
                r = self._http.request(method, self.base_url + path, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code < 500 and r.status_code != 429:
                if r.status_code in (401, 403):
                    log.error("TrustBench: the API key was refused. Tracing is switched off.")
                    self.enabled = False
                return r
            if attempt < self.retries - 1:
                time.sleep(delay)
                delay *= 2
        return None

    def _send_traces(self, traces: list[dict]) -> None:
        r = self._request("POST", "/v1/traces", {"traces": traces})
        if r is None or r.status_code in (401, 403):
            self.stats.dropped += len(traces)
        elif r.status_code == 413 and len(traces) > 1:
            half = len(traces) // 2
            self._send_traces(traces[:half])
            self._send_traces(traces[half:])
        elif r.status_code == 202:
            body = r.json()
            self.stats.sent += body.get("accepted", 0)
            self.stats.duplicates += body.get("duplicates", 0)
            self.stats.rejected += len(body.get("rejected", []))
            if body.get("rejected"):
                log.warning("TrustBench: the server refused %d trace(s): %s", len(body["rejected"]), body["rejected"][0]["reason"])
        else:
            self.stats.dropped += len(traces)

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self.flush()
        except Exception:
            log.debug("TrustBench: flush at close failed", exc_info=True)
