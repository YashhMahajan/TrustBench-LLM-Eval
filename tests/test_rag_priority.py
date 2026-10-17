import pytest

from trustbench.failures import Failure
from trustbench.priority import WEIGHT, case_severity, prioritize
from trustbench.rag import diagnose, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from trustbench.rundata import Record

CTX = "The shop opens at 9 am. Parking is free for 2 hours. Dogs are welcome inside. The cafe closes at 5 pm."


def res(evaluator, passed=False, **details):
    return {"evaluator": evaluator, "passed": passed, "score": float(passed), "details": details, "rationale": "r"}


def rec(results, context=CTX, expected=None, **kw):
    return Record(case_id="c", input={"context": context, "question": "q"}, expected=expected or {"required_facts": ["2 hours"]},
                  results=results, passed=all(r["passed"] for r in results), **kw)


def test_retrieval_metrics_known_values():
    got, gold = ["d3", "d1", "d9", "d2"], ["d1", "d2"]
    assert recall_at_k(got, gold, 2) == 0.5 and recall_at_k(got, gold, 4) == 1.0
    assert precision_at_k(got, gold, 2) == 0.5 and precision_at_k(got, gold, 4) == 0.5
    assert reciprocal_rank(got, gold) == 0.5 and reciprocal_rank(["x"], gold) == 0.0
    assert ndcg_at_k(["d1", "d2"], gold, 5) == 1.0
    assert 0.5 < ndcg_at_k(got, gold, 5) < 1.0
    assert recall_at_k(got, [], 3) is None and ndcg_at_k(got, [], 3) is None and precision_at_k([], gold, 3) is None


def test_missing_evidence_is_a_retrieval_failure():
    d = diagnose(rec([res("contains")], context="The shop opens at 9 am. Dogs are welcome inside."))
    assert d.stage == "retrieval_failure" and d.evidence_in_context is False and "never reached" in d.explanation


def test_wrong_answer_with_evidence_present_blames_the_model():
    d = diagnose(rec([res("contains")]))
    assert d.stage == "evidence_ignored" and d.evidence_in_context and d.evidence_position == pytest.approx(1 / 3)
    assert "33%" in d.explanation


def test_ungrounded_claims_are_a_separate_stage():
    d = diagnose(rec([res("contains", True), res("grounded_claims", claims=[{"label": "insufficient"}])]))
    assert d.stage == "ungrounded_generation"


def test_abstention_cases():
    exp = {"abstain": True}
    assert diagnose(rec([res("abstain", failure="failure_to_abstain")], expected=exp)).stage == "failure_to_abstain"
    assert diagnose(rec([res("abstain", True)], expected=exp)).stage is None


def test_passing_and_context_free_cases_have_no_stage():
    assert diagnose(rec([res("contains", True)])).stage is None
    plain = Record(case_id="p", input={"prompt": "q"}, results=[res("exact_match")])
    d = diagnose(plain)
    assert d.stage is None and d.evidence_in_context is None


def test_retrieval_ids_decide_when_given_and_metrics_are_reported():
    r = Record(case_id="r", input={"context": "x", "retrieved_ids": ["d3", "d1"]}, expected={"gold_doc_ids": ["d1"], "required_facts": ["x"]},
               results=[res("contains")])
    d = diagnose(r)
    assert d.stage == "evidence_ignored" and d.retrieval["mrr"] == 0.5 and d.retrieval["recall@5"] == 1.0
    r.input["retrieved_ids"] = ["d3", "d4"]
    assert diagnose(r).stage == "retrieval_failure"


def fail(labels, meta=None, judged=False, case_id="c"):
    r = Record(case_id=case_id, metadata=meta or {}, results=[res("llm_judge" if judged else "exact_match")])
    return Failure(case_id, "m", "t", labels, [], None, record=r)


def test_severity_is_the_highest_of_failure_type_and_case():
    assert case_severity(fail(["format_violation"])) == 2
    assert case_severity(fail(["format_violation"], {"risk": "high"})) == 4
    assert case_severity(fail(["format_violation"], {"critical": True})) == 5
    assert case_severity(fail(["wrong_answer"], {"severity": 4})) == 4
    assert case_severity(fail(["prompt_injection_failure"])) == 5


def test_a_rare_severe_failure_can_outrank_a_frequent_small_one():
    groups = {"format": [fail(["format_violation"], case_id=f"f{i}") for i in range(10)],
              "injection": [fail(["prompt_injection_failure"], case_id=f"i{i}") for i in range(2)]}
    items = prioritize(groups, total_cases=100)
    assert [i.name for i in items] == ["injection", "format"]
    inj = items[0]
    assert (inj.severity, inj.weight, inj.frequency_pct, inj.cases) == (5, WEIGHT[5], 2.0, 2)
    assert inj.score == pytest.approx(16 * 2.0) and items[1].score == pytest.approx(2 * 10.0)


def test_exposure_and_detectability_change_the_order():
    groups = {"a": [fail(["wrong_answer"], {"exposure": 3.0}, case_id="a")], "b": [fail(["wrong_answer"], case_id="b")],
              "c": [fail(["wrong_answer"], judged=True, case_id="c")]}
    items = {i.name: i for i in prioritize(groups, 10)}
    assert items["a"].score == pytest.approx(3 * items["b"].score) and items["c"].score == pytest.approx(1.5 * items["b"].score)
    assert items["c"].detectability == 1.5 and items["a"].exposure == 3.0


def test_empty_groups_are_skipped():
    assert prioritize({"x": []}, 10) == [] and prioritize({"x": [fail(["wrong_answer"])]}, 0)[0].frequency_pct == 0.0
