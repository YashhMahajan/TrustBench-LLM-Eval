"""The engine at scale: cache, resume, Ctrl-C, rate limit, Retry-After, closing connections, stored overview."""

import asyncio
import signal
import time

import httpx
import pytest
from sqlalchemy import event, func, select

from test_engine import ANSWERS, go
from trustbench import engine, report
from trustbench.db import EvaluationRun, ModelResponse, ResponseCache, TestExecution
from trustbench.engine import RunInterrupted, RunOptions, resume_run, run_eval
from trustbench.overview import get_overview
from trustbench.providers import GenRequest, MockProvider, ProviderError
from trustbench.providers.base import parse_retry_after, raise_for_status
from trustbench.providers.http import OpenAICompatibleProvider, is_reasoning_model


def cacheable(provider: MockProvider) -> MockProvider:
    provider.cacheable = True          # Mock answers depend on how we set up the mock, so the cache skips it by default.
    return provider


# ---- Cache ----------------------------------------------------------------------------------------

def test_the_same_request_is_answered_from_the_cache_the_second_time(factory, version):
    provider = cacheable(MockProvider(ANSWERS))
    first = go(factory, version, provider)
    assert provider.calls == 3
    second = go(factory, version, provider)
    assert provider.calls == 3                                  # No new model call.
    assert [r["passed"] for r in report.case_rows(factory, first)] == [r["passed"] for r in report.case_rows(factory, second)]
    assert {r["cost_usd"] for r in report.case_rows(factory, second)} <= {0.0}
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ResponseCache)) == 3


def test_the_cache_can_be_turned_off(factory, version):
    provider = cacheable(MockProvider(ANSWERS))
    go(factory, version, provider)
    go(factory, version, provider, cache=False)
    assert provider.calls == 6


def test_a_changed_setting_is_a_different_request(factory, version):
    provider = cacheable(MockProvider(ANSWERS))
    go(factory, version, provider)
    go(factory, version, provider, max_tokens=10)
    go(factory, version, provider, system_prompt="Be short.")
    assert provider.calls == 9


def test_repetitions_stay_separate_calls_so_variance_can_be_measured(factory, version):
    provider = cacheable(MockProvider(ANSWERS))
    go(factory, version, provider, repetitions=3)
    assert provider.calls == 9
    go(factory, version, provider, repetitions=3)
    assert provider.calls == 9


def test_a_failed_call_is_never_cached(factory, version):
    provider = cacheable(MockProvider(always_fail=True))
    go(factory, version, provider)
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ResponseCache)) == 0


# ---- Resume and Ctrl-C ----------------------------------------------------------------------------

def test_a_run_stopped_by_the_budget_can_be_finished(factory, version, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_PRICES", '{"m": [1000000, 1000000]}')
    provider = MockProvider(ANSWERS)
    rid = go(factory, version, provider, concurrency=1, budget_usd=0.0001)
    assert report.get_run(factory, rid).status == "incomplete"
    resume_run(factory, rid, provider_factory=lambda n: provider, budget_usd=100)
    rows = report.case_rows(factory, rid)
    assert [r["status"] for r in rows].count("done") == 3
    assert report.get_run(factory, rid).status == "complete"
    assert provider.calls == 3                                  # The run called each case once. The run repeated nothing.


def test_resume_refuses_a_run_with_nothing_left(factory, version):
    rid = go(factory, version, MockProvider(ANSWERS))
    with pytest.raises(ValueError, match="nothing left"):
        resume_run(factory, rid, provider_factory=lambda n: MockProvider(ANSWERS))
    with pytest.raises(ValueError, match="No run"):
        resume_run(factory, "nope", provider_factory=lambda n: MockProvider(ANSWERS))


def test_ctrl_c_keeps_the_saved_results_and_resume_does_the_rest(factory, version):
    class StopsOnSecondCall(MockProvider):
        async def generate(self, req):
            if self.calls == 1:
                self.calls += 1
                signal.raise_signal(signal.SIGINT)              # What Ctrl-C does.
                await asyncio.sleep(5)                          # The run stops here.
            return await super().generate(req)

    provider = StopsOnSecondCall(ANSWERS)
    with pytest.raises(RunInterrupted) as stopped:
        run_eval(factory, version.id, ["mock:m"], RunOptions(backoff=0, concurrency=1), provider_factory=lambda n: provider)
    rid = stopped.value.run_id
    run = report.get_run(factory, rid)
    assert run.status == "incomplete" and run.finished_at is not None
    with factory() as s:
        done = s.scalar(select(func.count()).select_from(TestExecution).where(TestExecution.status == "done"))
    assert done == 1                                            # The run saved the first result. It did not lose it.
    resume_run(factory, rid, provider_factory=lambda n: MockProvider(ANSWERS))
    assert {r["status"] for r in report.case_rows(factory, rid)} == {"done"}
    assert report.get_run(factory, rid).status == "complete"


def test_a_result_is_saved_whole_or_not_at_all(factory, version):
    """Write-behind keeps all attempts of one case in one transaction."""
    rid = go(factory, version, MockProvider(ANSWERS, fail_first=1))
    with factory() as s:
        attempts = s.execute(select(ModelResponse.execution_id, func.count()).group_by(ModelResponse.execution_id)).all()
    assert all(n == 2 for _, n in attempts) and len(attempts) == 3
    assert rid


# ---- Rate limit, Retry-After, closing -------------------------------------------------------------

def test_the_rate_limit_spaces_the_calls():
    limiter = engine._Limiter(600)                              # One call each 0.1 s.
    async def three():
        for _ in range(3):
            await limiter.wait()
    t = time.perf_counter()
    asyncio.run(three())
    assert time.perf_counter() - t >= 0.19                      # The first call is free. Two gaps remain.
    assert engine._Limiter(None).gap == 0


def test_the_wait_asked_by_the_server_is_respected_but_capped(factory, version, monkeypatch):
    class Slowed(MockProvider):
        async def generate(self, req):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("slow down", retryable=True, retry_after=0.3)
            return await super().generate(req)

    t = time.perf_counter()
    go(factory, version, Slowed(ANSWERS), concurrency=1, retries=1)
    assert time.perf_counter() - t >= 0.29                      # The backoff setting is 0. The run used the wish of the server.

    monkeypatch.setattr(engine, "MAX_RETRY_WAIT", 0.01)

    class Hostile(MockProvider):
        async def generate(self, req):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("wait a day", retryable=True, retry_after=86400)
            return await super().generate(req)

    t = time.perf_counter()
    go(factory, version, Hostile(ANSWERS), concurrency=1, retries=1)
    assert time.perf_counter() - t < 5


def test_connections_are_closed_when_the_run_ends_even_after_an_error(factory, version):
    class Tracked(MockProvider):
        closed = 0
        async def aclose(self):
            Tracked.closed += 1

    go(factory, version, Tracked(ANSWERS))
    assert Tracked.closed == 1
    go(factory, version, Tracked(always_fail=True))
    assert Tracked.closed == 2


def test_the_run_uses_a_fixed_number_of_statements(factory, version):
    engine_ = factory.kw["bind"]
    count = []
    event.listen(engine_, "before_cursor_execute", lambda *a, **k: count.append(1))
    go(factory, version, MockProvider(ANSWERS))
    small = len(count)
    assert small < 60


# ---- Provider details -----------------------------------------------------------------------------

def test_retry_after_header_is_read():
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after("0.5") == 0.5
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after(None) is None
    assert parse_retry_after("-3") is None
    with pytest.raises(ProviderError) as e:
        raise_for_status(429, "slow", {"retry-after": "7"})
    assert e.value.retry_after == 7.0 and e.value.retryable


@pytest.mark.parametrize("model,reasoning", [("o3-mini", True), ("o1", True), ("gpt-5", True), ("gpt-5-mini", True),
                                             ("gpt-4o-mini", False), ("gpt-4.1", False), ("llama3", False)])
def test_reasoning_models_are_recognised(model, reasoning):
    assert is_reasoning_model(model) is reasoning


@pytest.mark.parametrize("model,field,absent", [("o3-mini", "max_completion_tokens", "max_tokens"),
                                                ("gpt-4o-mini", "max_tokens", "max_completion_tokens")])
def test_openai_requests_use_the_right_token_field(model, field, absent):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}, "model": model})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("openai", "k", "https://x", client)
    asyncio.run(provider.generate(GenRequest(model=model, prompt="hi", temperature=0.7, max_tokens=33)))
    assert seen[field] == 33 and absent not in seen
    assert ("temperature" in seen) is (not is_reasoning_model(model))


def test_a_provider_closes_only_the_client_it_made():
    shared = httpx.AsyncClient()
    mine = OpenAICompatibleProvider("openai", "k", "https://x", shared)
    asyncio.run(mine.aclose())
    assert not shared.is_closed
    owner = OpenAICompatibleProvider("openai", "k", "https://x")
    asyncio.run(owner.aclose())
    assert owner.client.is_closed
    asyncio.run(shared.aclose())


# ---- Stored overview ------------------------------------------------------------------------------

def test_the_overview_is_stored_when_a_run_ends(factory, version):
    rid = go(factory, version, MockProvider(ANSWERS))
    run = report.get_run(factory, rid)
    assert set(run.overview) == {"mock:m"}
    assert run.overview["mock:m"]["n"] == 3 and run.overview["mock:m"]["rate"] == pytest.approx(2 / 3)


def test_reading_a_stored_overview_needs_no_query_and_an_old_run_gets_one(factory, version):
    rid = go(factory, version, MockProvider(ANSWERS))
    with factory() as s:
        run = s.get(EvaluationRun, rid)
    count = []
    event.listen(factory.kw["bind"], "before_cursor_execute", lambda *a, **k: count.append(1))
    assert get_overview(factory, run)["mock:m"]["n"] == 3
    assert count == []
    with factory.begin() as s:
        s.get(EvaluationRun, rid).overview = None               # A run from an older version.
    with factory() as s:
        old = s.get(EvaluationRun, rid)
    assert get_overview(factory, old)["mock:m"]["n"] == 3
    assert report.get_run(factory, rid).overview is not None


def test_scoring_again_refreshes_the_overview(factory, version):
    rid = go(factory, version, MockProvider(ANSWERS))
    with factory.begin() as s:
        s.get(EvaluationRun, rid).overview = {"stale": True}
    engine.rescore(factory, rid)
    assert "mock:m" in report.get_run(factory, rid).overview


# ---- Commands -------------------------------------------------------------------------------------

def test_cache_and_resume_commands(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from trustbench.cli import app
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    runner = CliRunner(env={"COLUMNS": "200"})
    out = runner.invoke(app, ["cache", "stats"])
    assert out.exit_code == 0 and "0 stored answers" in out.output
    out = runner.invoke(app, ["run", "--model", "mock:smoke", "--rpm", "6000", "--no-cache"])
    assert out.exit_code == 0 and "Pass rate" in out.output
    run_id = next(w for w in out.output.split() if len(w) == 12 and w.isalnum() and w != "Pass_rate" and any(c.isdigit() for c in w))
    out = runner.invoke(app, ["resume", run_id])
    assert out.exit_code == 1 and "nothing left" in out.output
    assert runner.invoke(app, ["resume", "nope"]).exit_code == 1
    assert runner.invoke(app, ["cache", "clear", "--yes"]).output.startswith("Deleted 0")
    assert runner.invoke(app, ["cache", "clear", "--yes", "--model", "openai:gpt-4o-mini"]).exit_code == 0
    assert runner.invoke(app, ["cache", "clear"], input="n\n").exit_code == 1


def test_the_database_cache_commands_work_on_stored_answers(tmp_path, monkeypatch, factory, version):
    from typer.testing import CliRunner
    from trustbench.cli import app
    from trustbench.db import init_db, make_engine
    path = f"{tmp_path}/d.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{path}")
    f2 = init_db(make_engine(f"sqlite:///{path}"))
    from trustbench.datasets import CaseSpec, import_dataset
    with f2() as s:
        v = import_dataset(s, "t", [CaseSpec(id="a", input="Capital of France?", expected={"answer": "Paris"})])
    provider = cacheable(MockProvider({"Capital": "Paris"}))
    run_eval(f2, v.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n: provider)
    runner = CliRunner(env={"COLUMNS": "200"})
    assert "1 stored answers" in runner.invoke(app, ["cache", "stats"]).output
    assert "Deleted 1" in runner.invoke(app, ["cache", "clear", "--yes", "--model", "mock:m"]).output
    assert "0 stored answers" in runner.invoke(app, ["cache", "stats"]).output
