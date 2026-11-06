"""App targets (M1): evaluate your own Python function or HTTP service, with a safe cache and no server-side code."""

import asyncio
import hashlib
import json
import pathlib

import httpx
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.datasets import CaseSpec, import_dataset
from trustbench.db import ModelResponse
from trustbench.engine import RunOptions, cache_key, run_eval
from trustbench.evaluators import EVALUATORS, Outcome, register
from trustbench.providers import GenRequest, ProviderError, allow_app_targets, get_provider
from trustbench.providers import app as app_mod
from trustbench.providers.app import HttpAppProvider, PythonAppProvider, code_fingerprint, split_version
from trustbench.providers.recorder import span, tool_call

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "trustbench"
REQ = lambda model, prompt="hi", **kw: GenRequest(model=model, prompt=prompt, **kw)  # noqa: E731


@pytest.fixture
def sample_app(tmp_path, monkeypatch):
    """A tiny app on the import path: plain, async, dict result, instrumented, crashing, wrong return type."""
    (tmp_path / "sample_app.py").write_text('''
from trustbench.providers.recorder import span, tool_call

def plain(prompt): return "Paris"
async def aplain(prompt): return "Rome"
def as_dict(prompt): return {"output": "done", "tool_calls": [{"name": "t", "args": {}}], "spans": [{"kind": "tool", "name": "t"}]}
def wants_input(prompt, case_input=None): return str(case_input)
def instrumented(prompt):
    with span("retrieval", "kb", query=prompt):
        pass
    tool_call("lookup", {"id": prompt}, result="ok")
    return "answered"
async def interleaved(prompt):
    import asyncio
    tool_call("first", {"id": prompt})
    with span("retrieval", "kb", query=prompt):
        await asyncio.sleep(0.01)          # Other cases run now. A shared recorder would mix them up.
    tool_call("second", {"id": prompt})
    return prompt
def boom(prompt): raise RuntimeError("bug in the app")
def wrong_type(prompt): return 42
def no_output(prompt): return {"tool_calls": []}
''')
    monkeypatch.syspath_prepend(str(tmp_path))
    return "sample_app"


def gen(model, **kw):
    return asyncio.run(PythonAppProvider().generate(REQ(model, **kw)))


# ---- Python target ----------------------------------------------------------------------------------

def test_a_plain_function_gives_text(sample_app):
    r = gen("sample_app:plain")
    assert r.text == "Paris" and r.tool_calls is None and r.spans is None


def test_an_async_function_is_awaited(sample_app):
    assert gen("sample_app:aplain").text == "Rome"


def test_a_dict_result_carries_tool_calls_and_spans(sample_app):
    r = gen("sample_app:as_dict")
    assert r.text == "done" and r.tool_calls[0]["name"] == "t" and r.spans[0]["kind"] == "tool"


def test_instrumented_code_is_recorded_without_returning_anything_special(sample_app):
    r = gen("sample_app:instrumented", prompt="q1")
    assert r.tool_calls == [{"name": "lookup", "args": {"id": "q1"}, "result": "ok", "error": None}]
    assert r.spans[0]["kind"] == "retrieval" and r.spans[0]["data"] == {"query": "q1"} and r.spans[0]["ms"] >= 0


def test_the_function_can_ask_for_the_full_case_input(sample_app):
    assert gen("sample_app:wants_input", input={"messages": [1]}).text == "{'messages': [1]}"


def test_the_version_label_is_ignored_when_loading_the_function(sample_app):
    assert gen("sample_app:plain@v7").text == "Paris"


@pytest.mark.parametrize("model,needle", [
    ("sample_app:boom", "bug in the app"),
    ("sample_app:wrong_type", "int"),
    ("sample_app:no_output", "without 'output'"),
    ("sample_app:missing", "Cannot load"),
    ("no_such_module_xyz:fn", "Cannot load"),
    ("justaname", "must look like"),
])
def test_app_problems_become_non_retryable_provider_errors(sample_app, model, needle):
    with pytest.raises(ProviderError) as e:
        gen(model)
    assert needle in str(e.value) and e.value.retryable is False


def test_concurrent_cases_never_see_each_others_records(sample_app):
    """Without isolation, a span from case A would be scored against case B, and nobody would notice."""
    async def many():
        p = PythonAppProvider()
        return await asyncio.gather(*(p.generate(REQ("sample_app:interleaved", prompt=f"q{i}")) for i in range(20)))
    for i, r in enumerate(asyncio.run(many())):
        assert [(c["name"], c["args"]["id"]) for c in r.tool_calls] == [("first", f"q{i}"), ("second", f"q{i}")]
        assert [s["data"]["query"] for s in r.spans] == [f"q{i}"]


def test_the_recorder_does_nothing_outside_an_evaluation_and_rejects_bad_kinds():
    tool_call("x")                                   # No recording is active: no error, nothing kept.
    with span("tool", "x"):
        pass
    from trustbench.providers.recorder import recording
    with recording():
        with pytest.raises(ValueError, match="Span kind"):
            with span("banana", "x"):
                pass


def test_a_failing_span_is_recorded_with_its_error():
    from trustbench.providers.recorder import recording
    with recording() as rec:
        with pytest.raises(KeyError):
            with span("tool", "x"):
                raise KeyError("k")
    assert "KeyError" in rec.spans[0]["error"]


# ---- HTTP target ------------------------------------------------------------------------------------

def http(handler):
    return HttpAppProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_http_target_sends_prompt_and_input_and_reads_a_structured_answer():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.read()))
        return httpx.Response(200, json={"output": "ok", "tool_calls": [{"name": "t"}], "spans": [{"kind": "tool", "name": "t"}]})

    r = asyncio.run(http(handler).generate(REQ("https://app.test/ask@v1", prompt="hello", input={"a": 1})))
    assert seen == {"prompt": "hello", "input": {"a": 1}}
    assert r.text == "ok" and r.tool_calls == [{"name": "t"}] and r.model_id == "https://app.test/ask"


def test_http_target_accepts_plain_text():
    r = asyncio.run(http(lambda req: httpx.Response(200, text="just text")).generate(REQ("https://app.test/ask")))
    assert r.text == "just text"


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True), (400, False), (404, False)])
def test_http_status_decides_retry(status, retryable):
    with pytest.raises(ProviderError) as e:
        asyncio.run(http(lambda req: httpx.Response(status)).generate(REQ("https://app.test/ask")))
    assert e.value.retryable is retryable


def test_http_timeout_and_unreachable_are_retryable():
    def timeout(req):
        raise httpx.ReadTimeout("slow")

    def down(req):
        raise httpx.ConnectError("refused")
    for h in (timeout, down):
        with pytest.raises(ProviderError) as e:
            asyncio.run(http(h).generate(REQ("https://app.test/ask")))
        assert e.value.retryable is True


# ---- Version labels and the cache fingerprint -------------------------------------------------------

@pytest.mark.parametrize("model,expected", [
    ("pkg.mod:fn@v1", ("pkg.mod:fn", "v1")),
    ("pkg.mod:fn", ("pkg.mod:fn", None)),
    ("https://a.test/x@v2", ("https://a.test/x", "v2")),
    ("https://user@a.test/x", ("https://user@a.test/x", None)),      # An '@' inside a URL is not a version.
    ("pkg.mod:fn@", ("pkg.mod:fn@", None)),
])
def test_split_version(model, expected):
    assert split_version(model) == expected


@pytest.mark.parametrize("state,version,expected", [
    (("abc", False), None, "git=abc"),           # Clean tree: the commit identifies the code.
    (("abc", True), None, None),                 # Dirty and no label: we cannot tell, so no cache.
    ((None, True), None, None),                  # Not a git checkout and no label: no cache.
    (("abc", True), "v1", "v=v1|git=abc"),       # A label is the user's word that the code is the same.
    ((None, True), "v1", "v=v1|git=none"),
])
def test_code_fingerprint(monkeypatch, state, version, expected):
    monkeypatch.setattr(app_mod, "_git_state", lambda: state)
    assert code_fingerprint(version) == expected


def test_a_http_target_without_a_label_is_not_cached():
    assert HttpAppProvider().cache_salt("https://a.test/x") is None
    assert HttpAppProvider().cache_salt("https://a.test/x@v1").startswith("v=v1|url=")


def test_cache_keys_of_model_answers_are_unchanged():
    """Regression: adding the salt must not change a single key that already exists in a user's database."""
    old = hashlib.sha256(json.dumps(["openai", "m", "p", None, 0.0, 100, 0]).encode()).hexdigest()
    assert cache_key("openai", "m", "p", None, 0.0, 100, 0) == old
    assert cache_key("openai", "m", "p", None, 0.0, 100, 0, "v=1") != old


# ---- End to end through the engine ------------------------------------------------------------------

@pytest.fixture
def seen_ctx():
    seen = []

    @register("capture_ctx")
    def capture(ctx):
        seen.append((ctx.output, list(ctx.tool_calls), list(ctx.spans)))
        return Outcome("capture_ctx", "1", True, 1.0, "ok")
    yield seen
    EVALUATORS.pop("capture_ctx")


def app_run(factory, version, spec, **opt):
    allow_app_targets()
    return run_eval(factory, version.id, [spec], RunOptions(backoff=0, **opt))


@pytest.fixture
def one_case(factory):
    with factory() as s:
        return import_dataset(s, "app", [CaseSpec(id="a", input="q9", evaluation={"evaluators": [{"type": "capture_ctx"}]})])


def test_tool_calls_and_spans_are_stored_and_reach_the_evaluators(factory, one_case, sample_app, seen_ctx):
    app_run(factory, one_case, "python:sample_app:instrumented@v1")
    assert seen_ctx[0][0] == "answered" and seen_ctx[0][1][0]["name"] == "lookup" and seen_ctx[0][2][0]["kind"] == "retrieval"
    with factory() as s:
        row = s.scalars(select(ModelResponse)).one()
        assert row.tool_calls[0]["args"] == {"id": "q9"} and row.spans[0]["name"] == "kb"


def test_the_same_label_replays_from_the_cache_with_its_tool_calls(factory, one_case, sample_app, seen_ctx, monkeypatch):
    monkeypatch.setattr(app_mod, "_git_state", lambda: ("abc", False))
    app_run(factory, one_case, "python:sample_app:instrumented@v1")
    # Second run: break the function. A cache hit never calls it, so the result must still be the stored one.
    monkeypatch.setattr(PythonAppProvider, "generate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    app_run(factory, one_case, "python:sample_app:instrumented@v1")
    assert seen_ctx[1] == seen_ctx[0]


def test_a_new_label_or_new_commit_does_not_replay_old_answers(factory, one_case, sample_app, seen_ctx, monkeypatch):
    monkeypatch.setattr(app_mod, "_git_state", lambda: ("abc", False))
    app_run(factory, one_case, "python:sample_app:plain@v1")
    calls = []
    real = PythonAppProvider.generate

    async def counting(self, req):
        calls.append(req.model)
        return await real(self, req)
    monkeypatch.setattr(PythonAppProvider, "generate", counting)
    app_run(factory, one_case, "python:sample_app:plain@v2")           # New label: miss.
    monkeypatch.setattr(app_mod, "_git_state", lambda: ("def", False))
    app_run(factory, one_case, "python:sample_app:plain@v2")           # New commit: miss.
    assert len(calls) == 2


def test_a_dirty_tree_without_a_label_is_never_cached(factory, one_case, sample_app, seen_ctx, monkeypatch):
    monkeypatch.setattr(app_mod, "_git_state", lambda: ("abc", True))
    calls = []
    real = PythonAppProvider.generate

    async def counting(self, req):
        calls.append(1)
        return await real(self, req)
    monkeypatch.setattr(PythonAppProvider, "generate", counting)
    app_run(factory, one_case, "python:sample_app:plain")
    app_run(factory, one_case, "python:sample_app:plain")
    assert len(calls) == 2


def test_a_crashing_app_fails_the_case_without_retrying(factory, one_case, sample_app, seen_ctx):
    from trustbench import report
    run_id = app_run(factory, one_case, "python:sample_app:boom@v1")
    rows = report.case_rows(factory, run_id)
    assert rows[0]["status"] == "failed" and rows[0]["attempts"] == 1


# ---- Safety: the server never runs your code --------------------------------------------------------

@pytest.mark.parametrize("spec", ["python", "http"])
def test_app_targets_are_refused_unless_the_cli_turned_them_on(spec):
    with pytest.raises(ValueError, match="only from the CLI and pytest"):
        get_provider(spec)
    allow_app_targets()
    assert get_provider(spec) is not None


def test_a_server_side_judge_cannot_be_a_python_target():
    """online.py builds its judge from a workspace setting. Anyone who can write that setting must not get code execution."""
    from trustbench.engine import make_judge
    with pytest.raises(ValueError, match="only from the CLI and pytest"):
        make_judge("python:os:system", get_provider)


def test_no_server_module_turns_app_targets_on():
    """If one of these ever calls allow_app_targets, an API key could run code on the server."""
    for name in ["api.py", "api_data.py", "handlers.py", "online.py", "jobs.py", "cli_server.py", "tenancy.py", "monitor.py"]:
        assert "allow_app_targets" not in (SRC / name).read_text(), name


def test_the_run_command_can_test_a_python_function(tmp_path, sample_app, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    data = tmp_path / "d.jsonl"
    data.write_text(json.dumps({"id": "a", "input": "Capital of France?", "expected": {"answer": "Paris"}}) + "\n")
    r = CliRunner(env={"COLUMNS": "200"}).invoke(app, ["run", "--dataset", str(data), "--model", "python:sample_app:plain@v1"])
    assert r.exit_code == 0, r.output
    assert "100%" in r.output
