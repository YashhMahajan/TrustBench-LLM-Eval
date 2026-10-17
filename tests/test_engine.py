from sqlalchemy import func, select

from trustbench import report
from trustbench.db import EvaluationResult, ModelResponse
from trustbench.evaluators import EVALUATORS, Outcome
from trustbench.engine import RunOptions, rescore, run_eval
from trustbench.providers import MockProvider

ANSWERS = {"Capital": "Paris", "2+2": "5", "Say hi": "hi there"}  # "2+2" is wrong on purpose.


def go(factory, version, provider, specs=("mock:m",), **opt):
    opt.setdefault("backoff", 0)
    return run_eval(factory, version.id, list(specs), RunOptions(**opt), provider_factory=lambda name: provider)


def test_run_scores_every_case(factory, version):
    run_id = go(factory, version, MockProvider(ANSWERS))
    rows = report.case_rows(factory, run_id)
    assert {r["case_id"]: r["passed"] for r in rows} == {"a": True, "b": False, "c": True}
    assert report.get_run(factory, run_id).status == "complete"


def test_retry_keeps_the_failed_attempt(factory, version):
    run_id = go(factory, version, MockProvider(ANSWERS, fail_first=1))
    rows = report.case_rows(factory, run_id)
    assert all(r["attempts"] == 2 and r["status"] == "done" for r in rows)
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ModelResponse).where(ModelResponse.ok.is_(False))) == 3


def test_permanent_error_is_recorded_not_raised(factory, version):
    run_id = go(factory, version, MockProvider(always_fail=True))
    rows = report.case_rows(factory, run_id)
    assert all(r["status"] == "failed" and r["passed"] is None and r["attempts"] == 1 for r in rows)
    assert report.get_run(factory, run_id).status == "complete"


def test_retries_stop_at_the_limit(factory, version):
    run_id = go(factory, version, MockProvider(ANSWERS, fail_first=9), retries=1)
    assert all(r["attempts"] == 2 and r["status"] == "failed" for r in report.case_rows(factory, run_id))


def test_budget_leaves_run_incomplete(factory, version, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_PRICES", '{"m": [1000000, 1000000]}')  # Every call costs real money.
    run_id = go(factory, version, MockProvider(ANSWERS), concurrency=1, budget_usd=0.0001)
    statuses = [r["status"] for r in report.case_rows(factory, run_id)]
    assert statuses.count("skipped") == 2 and statuses.count("done") == 1
    assert report.get_run(factory, run_id).status == "incomplete"


def test_repetitions_and_many_models(factory, version):
    run_id = go(factory, version, MockProvider(ANSWERS), specs=("mock:m1", "mock:m2"), repetitions=2)
    rows = report.case_rows(factory, run_id)
    assert len(rows) == 3 * 2 * 2
    assert {m["model"] for m in report.summarize(rows)} == {"mock:m1", "mock:m2"}


def test_rescore_makes_no_model_calls_and_keeps_old_scores(factory, version, monkeypatch):
    provider = MockProvider(ANSWERS)
    run_id = go(factory, version, provider)
    calls = provider.calls
    # Change the evaluator. Now every exact_match passes, so "5" counts for the 2+2 case.
    lenient = lambda expected, output, spec: Outcome("exact_match", "2", True, 1.0, "lenient")
    monkeypatch.setitem(EVALUATORS, "exact_match", lenient)
    batch = rescore(factory, run_id)
    assert provider.calls == calls and batch == 1
    assert all(r["passed"] for r in report.case_rows(factory, run_id))
    assert not report.case_rows(factory, run_id, batch=0)[1]["passed"]  # The original score is still there.
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(EvaluationResult)) == 3 + 3


def test_config_snapshot_is_stored(factory, version):
    run_id = go(factory, version, MockProvider(ANSWERS), repetitions=2)
    snap = report.get_run(factory, run_id).config_snapshot
    assert snap["models"] == ["mock:m"] and snap["options"]["repetitions"] == 2
