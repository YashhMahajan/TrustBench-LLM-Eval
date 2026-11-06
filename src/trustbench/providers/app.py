"""App targets: test your own code, not only a bare model.

    trustbench run --model python:myapp.agent:answer@v1     (a Python function)
    trustbench run --model http:https://my-app.example/ask@v1   (a service you run)

The part after `@` is your version label. It goes into the answer cache key, so a new version never
replays old answers. Without a label we use the git commit, and we turn the cache off for a dirty tree.

The folder you run the command in is importable, so `python:myapp.agent:answer` finds ./myapp/agent.py.
A python target receives the prompt. It may return text, or a dict {"output", "tool_calls", "spans"}.
If its signature has a `case_input` parameter, it also receives the full case input (for example messages).
If it has a `system` parameter, it receives the system prompt of the run (`--system-prompt-file`), so you can test prompt changes.
"""

import functools
import hashlib
import importlib
import inspect
import os
import subprocess
import sys
import time

import httpx

from .base import GenRequest, GenResponse, Provider, ProviderError
from .recorder import recording


def split_version(model: str) -> tuple[str, str | None]:
    """'pkg.mod:fn@v1' -> ('pkg.mod:fn', 'v1'). An '@' that belongs to a URL path is not a version."""
    target, sep, version = model.rpartition("@")
    if not sep or not version or "/" in version or ":" in version:
        return model, None
    return target, version


@functools.lru_cache(maxsize=1)
def _git_state() -> tuple[str | None, bool]:
    """(HEAD commit, tree is dirty). (None, True) when this is not a git checkout."""
    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)  # noqa: S603, S607 - fixed git arguments
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    head = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    if head is None or status is None:
        return None, True
    return head, bool(status)


def code_fingerprint(version: str | None) -> str | None:
    """The cache salt for app code. None means: do not cache, we cannot tell the code is the same."""
    head, dirty = _git_state()
    if version:
        return f"v={version}|git={head or 'none'}"
    if head is None or dirty:
        return None
    return f"git={head}"


def _normalize(result, base_ms: float) -> tuple[str, list[dict], list[dict]]:
    if isinstance(result, dict):
        out = result.get("output")
        if out is None:
            raise ProviderError("The app returned a dict without 'output'.", retryable=False)
        return str(out), list(result.get("tool_calls") or []), list(result.get("spans") or [])
    if isinstance(result, str):
        return result, [], []
    raise ProviderError(f"The app returned {type(result).__name__}. Return text or a dict with 'output'.", retryable=False)


class PythonAppProvider(Provider):
    """Calls a Python function. One provider serves every `module:function` target."""

    name = "python"

    def __init__(self) -> None:
        self._fns: dict[str, object] = {}
        cwd = os.getcwd()
        if cwd not in sys.path:             # The `trustbench` script does not do this. Your project folder must be importable.
            sys.path.append(cwd)            # Last, not first: a stray json.py or yaml.py in a project cannot shadow the real one.

    def cache_salt(self, model: str) -> str | None:
        return code_fingerprint(split_version(model)[1])

    def _load(self, model: str):
        target, _ = split_version(model)
        if target not in self._fns:
            module, _, attr = target.partition(":")
            if not module or not attr:
                raise ProviderError(f"Target '{target}' must look like package.module:function.", retryable=False)
            try:
                self._fns[target] = getattr(importlib.import_module(module), attr)
            except (ImportError, AttributeError) as e:
                raise ProviderError(f"Cannot load '{target}': {e}", retryable=False) from e
        return self._fns[target]

    async def generate(self, req: GenRequest) -> GenResponse:
        fn = self._load(req.model)
        params = inspect.signature(fn).parameters
        kwargs = {**({"case_input": req.input} if "case_input" in params else {}),
                  **({"system": req.system} if "system" in params else {})}
        t0 = time.perf_counter()
        with recording() as rec:
            try:
                result = fn(req.prompt, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
            except ProviderError:
                raise
            except Exception as e:                          # A bug in the app is a failed case, not a retry.
                raise ProviderError(f"The app raised {e!r}", retryable=False) from e
        text, calls, spans = _normalize(result, 0.0)
        return GenResponse(text, None, None, (time.perf_counter() - t0) * 1000, req.model,
                           tool_calls=[*rec.tool_calls, *calls] or None, spans=[*rec.spans, *spans] or None)


class HttpAppProvider(Provider):
    """POSTs {"prompt", "input", "system"} to a service. It answers {"output", "tool_calls", "spans"} or plain text."""

    name = "http"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient()

    def cache_salt(self, model: str) -> str | None:
        target, version = split_version(model)
        if not version:
            return None                                     # We cannot see the code behind a URL. Ask for a label.
        return f"v={version}|url={hashlib.sha256(target.encode()).hexdigest()[:12]}"

    async def generate(self, req: GenRequest) -> GenResponse:
        url, _ = split_version(req.model)
        t0 = time.perf_counter()
        try:
            r = await self._client.post(url, json={"prompt": req.prompt, "input": req.input, "system": req.system}, timeout=req.timeout)
        except httpx.TimeoutException as e:
            raise ProviderError("The app timed out.", retryable=True) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"The app is not reachable: {e}", retryable=True) from e
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderError(f"The app answered {r.status_code}.", retryable=True)
        if r.status_code >= 400:
            raise ProviderError(f"The app answered {r.status_code}.", retryable=False)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        text, calls, spans = _normalize(body, 0.0)
        return GenResponse(text, None, None, (time.perf_counter() - t0) * 1000, url,
                           tool_calls=calls or None, spans=spans or None)

    async def aclose(self) -> None:
        await self._client.aclose()
