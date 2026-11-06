from trustbench.failures import TAXONOMY, classify, collect_failures, label_counts
from trustbench.rundata import Record


def res(evaluator, passed=False, rationale="r", **details):
    return {"evaluator": evaluator, "passed": passed, "score": float(passed), "details": details, "rationale": rationale}


def rec(*results, **kw):
    kw.setdefault("case_id", "c")
    return Record(results=list(results), passed=all(r["passed"] for r in results), **kw)


def test_each_evaluator_failure_has_its_label():
    cases = {
        "wrong_answer": res("exact_match"), "judged_incorrect": res("llm_judge"),
        "instruction_violation": res("rule"), "format_violation": res("json_schema"),
        "unnecessary_refusal": res("abstain", failure="unnecessary_refusal"),
        "failure_to_abstain": res("abstain", failure="failure_to_abstain"),
        "prompt_injection_failure": res("injection_resisted", obeyed=True),
        "task_not_done": res("injection_resisted", obeyed=False),
    }
    for label, r in cases.items():
        assert classify(rec(r)) == [label], label
    assert set(cases) <= set(TAXONOMY)


def test_passing_results_get_no_label():
    assert classify(rec(res("exact_match", True), res("rule", True))) == []


def test_several_failures_give_several_labels_without_repeats():
    assert classify(rec(res("exact_match"), res("rule"), res("contains"))) == ["wrong_answer", "instruction_violation"]


def test_claim_labels():
    r = rec(res("grounded_claims", claims=[{"label": "supported"}, {"label": "contradicted"}, {"label": "insufficient"}]))
    assert classify(r) == ["contradicted_claim", "unsupported_claim"]
    assert classify(rec(res("grounded_claims", claims=[{"label": "insufficient"}]))) == ["unsupported_claim"]


def test_calibration_without_a_confidence_is_a_format_problem():
    assert classify(rec(res("calibrated_answer", confidence=None, correct=True))) == ["format_violation"]
    assert classify(rec(res("calibrated_answer", confidence=0.9, correct=False))) == ["wrong_answer"]


def test_call_failures_timeouts_and_evaluator_errors():
    assert classify(Record(status="failed")) == ["call_failure"]
    assert classify(Record(status="failed", timed_out=True)) == ["call_failure", "timeout"]
    assert classify(Record(passed=None, evaluator_error=True, results=[res("llm_judge", error=True)])) == ["evaluation_error"]
    assert classify(Record(status="skipped")) == []


def test_collect_failures_has_reasons_output_and_review_flag():
    r = rec(res("exact_match", rationale="Expected one of ['4']."), res("grounded_claims", human_review=True, claims=[]),
            case_id="b", task="qa", output="5")
    [f] = collect_failures([r, rec(res("exact_match", True), case_id="ok")])
    assert (f.case_id, f.task, f.output, f.needs_review) == ("b", "qa", "5", True)
    assert f.reasons[0] == "Expected one of ['4']."


def test_the_first_failing_repetition_is_used_once():
    recs = [rec(res("exact_match", True), case_id="c", repetition=0), rec(res("exact_match"), case_id="c", repetition=1),
            rec(res("rule"), case_id="c", repetition=2)]
    [f] = collect_failures(recs)
    assert f.labels == ["wrong_answer"]


def test_a_failed_copy_of_a_passing_original_is_inconsistent_behavior():
    orig = rec(res("exact_match", True), case_id="a")
    copy = rec(res("exact_match"), case_id="a~typos", metadata={"source_case": "a", "transform": "typos"})
    wrong_both = [rec(res("exact_match"), case_id="b"), rec(res("exact_match"), case_id="b~typos", metadata={"source_case": "b"})]
    labels = {f.case_id: f.labels for f in collect_failures([orig, copy, *wrong_both])}
    assert labels["a~typos"] == ["inconsistent_behavior", "wrong_answer"]
    assert labels["b~typos"] == ["wrong_answer"]            # The original failed too, so this is not inconsistency.


def test_label_counts_use_cases_not_labels():
    recs = [rec(res("exact_match"), case_id=f"c{i}") for i in range(3)] + [rec(res("rule"), case_id="r")] + [rec(res("exact_match", True), case_id=f"p{i}") for i in range(6)]
    counts = label_counts(collect_failures(recs), total_cases=10)
    assert [(c.label, c.cases, c.share) for c in counts] == [("wrong_answer", 3, 0.3), ("instruction_violation", 1, 0.1)]
    assert counts[0].case_ids == ["c0", "c1", "c2"]


# ---- Agent labels -----------------------------------------------------------------------------------

def test_agent_failures_get_labels_that_say_how_the_agent_failed():
    def labels(ev, **details):
        return classify(rec(res(ev, **details)))
    assert labels("tool_call", bad_args=[{"tool": "refund"}]) == ["bad_args"]
    assert labels("tool_call", missing=["refund"]) == ["wrong_tool"]
    assert labels("tool_call", forbidden_called=["delete"]) == ["wrong_tool"]
    assert labels("tool_call", missing=["refund"], gave_up=True) == ["wrong_tool", "gave_up"]
    assert labels("trajectory", looped=True) == ["looped"]
    assert labels("trajectory", gave_up=True) == ["gave_up"]
    assert labels("trajectory") == ["wrong_tool"]
    assert labels("task_success") == ["task_not_completed"]
    assert labels("rag_answer_relevance") == ["irrelevant_answer"]
    assert labels("rag_context_recall") == ["poor_retrieval"] and labels("rag_context_precision") == ["poor_retrieval"]
    assert labels("rag_faithfulness", claims=[{"label": "contradicted"}]) == ["contradicted_claim"]


def test_every_agent_label_is_explained_and_has_a_priority():
    from trustbench.priority import LABEL_SEVERITY
    for label in ("wrong_tool", "bad_args", "looped", "gave_up", "task_not_completed", "irrelevant_answer", "poor_retrieval"):
        assert TAXONOMY[label] and label in LABEL_SEVERITY
