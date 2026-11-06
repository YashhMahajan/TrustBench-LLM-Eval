"""Record what your app does while TrustBench tests it: tool calls and spans.

Use these helpers inside the code you want to test:

    from trustbench.providers.recorder import tool_call, span

    with span("retrieval", "search_kb", query=q):
        docs = search(q)
    tool_call("lookup_order", {"order_id": "A1"}, result=order)

Outside an evaluation they do nothing, so you can leave them in production code.
Each evaluation case has its own recorder, so concurrent cases never see each other's records.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar

SPAN_KINDS = ("model", "retrieval", "tool", "other")     # The same kinds production traces use (traces.py).

_current: ContextVar["Recording | None"] = ContextVar("trustbench_recording", default=None)


class Recording:
    """The tool calls and spans of one case."""

    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.spans: list[dict] = []


@contextmanager
def recording():
    """Collect the records of one case. The app provider wraps each call in this."""
    rec = Recording()
    token = _current.set(rec)
    try:
        yield rec
    finally:
        _current.reset(token)


def tool_call(name: str, args: dict | None = None, result=None, error: str | None = None) -> None:
    """Note that the app called a tool. Order is kept."""
    rec = _current.get()
    if rec is not None:
        rec.tool_calls.append({"name": name, "args": args or {}, "result": result, "error": error})


@contextmanager
def span(kind: str, name: str, **data):
    """Time a step of the app. `kind` is model, retrieval, tool or other."""
    rec = _current.get()
    if rec is None:
        yield
        return
    if kind not in SPAN_KINDS:
        raise ValueError(f"Span kind must be one of {SPAN_KINDS}, not '{kind}'.")
    entry = {"kind": kind, "name": name, "data": data, "ms": 0.0, "error": None}
    t0 = time.perf_counter()
    try:
        yield entry["data"]
    except Exception as e:
        entry["error"] = repr(e)
        raise
    finally:
        entry["ms"] = (time.perf_counter() - t0) * 1000
        rec.spans.append(entry)
