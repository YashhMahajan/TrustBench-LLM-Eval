from test_engine import ANSWERS, go
from trustbench.engine import RunOptions
from trustbench.providers import MockProvider
from trustbench.rundata import Record, case_outcomes, load_run
import pytest


def test_load_run_has_everything_analysis_needs(factory, version):
    run_id = go(factory, version, MockProvider(ANSWERS), specs=("mock:m1", "mock:m2"))
    rd = load_run(factory, run_id)
    assert rd.status == "complete" and rd.dataset == "t" and rd.models == ["mock:m1", "mock:m2"]
    assert len(rd.records) == 6
    rec = next(r for r in rd.for_model("mock:m1") if r.case_id == "b")
    assert (rec.passed, rec.task, rec.input_words, rec.status) == (False, "generic", 1, "done")
    assert rec.latency_ms == 1.0 and rec.results[0]["evaluator"] == "exact_match"


def test_model_choice_must_be_clear(factory, version):
    rd = load_run(factory, go(factory, version, MockProvider(ANSWERS), specs=("mock:m1", "mock:m2")))
    with pytest.raises(ValueError, match="several models"):
        rd.for_model()
    with pytest.raises(ValueError, match="no model"):
        rd.for_model("mock:zzz")
    one = load_run(factory, go(factory, version, MockProvider(ANSWERS)))
    assert len(one.for_model()) == 3


def test_unknown_run_is_an_error(factory):
    with pytest.raises(ValueError, match="No run"):
        load_run(factory, "nope")


def test_failed_calls_and_skips_in_the_loaded_data(factory, version):
    rd = load_run(factory, go(factory, version, MockProvider(always_fail=True)))
    assert all(r.status == "failed" and r.passed is None for r in rd.records)


def test_case_outcomes_rules():
    recs = [
        Record(case_id="a", passed=True), Record(case_id="a", repetition=1, passed=False),   # 1 of 2
        Record(case_id="b", status="failed"),                                               # no answer = fail
        Record(case_id="c", status="skipped"),                                              # left out
        Record(case_id="d", passed=None, evaluator_error=True),                             # left out
    ]
    assert case_outcomes(recs) == {"a": 0.5, "b": 0.0, "c": None, "d": None}


def test_rescored_batch_is_used_by_default(factory, version, monkeypatch):
    from trustbench.engine import rescore
    from trustbench.evaluators import EVALUATORS, Outcome
    run_id = go(factory, version, MockProvider(ANSWERS))
    assert case_outcomes(load_run(factory, run_id).records)["b"] == 0.0
    monkeypatch.setitem(EVALUATORS, "exact_match", lambda ctx: Outcome("exact_match", "2", True, 1.0, "ok"))
    rescore(factory, run_id)
    assert case_outcomes(load_run(factory, run_id).records)["b"] == 1.0
    assert case_outcomes(load_run(factory, run_id, batch=0).records)["b"] == 0.0
