import random


from trustbench.gate import evaluate_gate
from trustbench.policy import ReleasePolicy, Rule
from trustbench.rundata import Record, RunData


def run(outcomes, task="qa", prefix="c", status="complete", latency=100.0, cost=None, **kw):
    recs = [Record(case_id=f"{prefix}{i}", task=task, passed=bool(o), latency_ms=latency, cost_usd=cost, **kw)
            for i, o in enumerate(outcomes)]
    return RunData("r", status, "d", "h", 0.0, recs)


def merge(*runs):
    return RunData("r", "complete", "d", "h", 0.0, [r for run_ in runs for r in run_.records])


def decide(policy, cand, base=None):
    return evaluate_gate(policy, cand, baseline=base)


def by_rule(decision):
    return {c.rule: c for c in decision.checks}


def policy(**kw):
    kw.setdefault("critical_failures_allowed", None)
    kw.setdefault("min_cases", 30)
    return ReleasePolicy(**kw)


def test_clear_pass():
    d = decide(policy(overall=Rule(minimum_score=0.8)), run([1] * 290 + [0] * 10))
    assert d.status == "pass" and d.allowed
    assert by_rule(d)["overall.minimum_score"].status == "pass"


def test_clear_fail_when_the_whole_interval_is_below():
    d = decide(policy(overall=Rule(minimum_score=0.9)), run([1] * 70 + [0] * 30))
    assert d.status == "fail" and not d.allowed
    assert "below" in by_rule(d)["overall.minimum_score"].evidence


def test_high_estimate_with_weak_evidence_is_inconclusive_not_pass():
    # 19 of 20 is 95%, above the 90% minimum, but the interval reaches far below 90%.
    d = decide(policy(min_cases=10, overall=Rule(minimum_score=0.9)), run([1] * 19 + [0]))
    assert d.status == "inconclusive" and not d.allowed
    assert "crosses" in by_rule(d)["overall.minimum_score"].evidence


def test_without_confidence_bound_the_point_estimate_decides():
    d = decide(policy(min_cases=10, confidence_bound=False, overall=Rule(minimum_score=0.9)), run([1] * 19 + [0]))
    assert d.status == "pass"


def test_allow_option_lets_inconclusive_go_on_but_keeps_the_status():
    d = decide(policy(min_cases=10, on_insufficient_evidence="allow", overall=Rule(minimum_score=0.9)), run([1] * 19 + [0]))
    assert d.status == "inconclusive" and d.allowed


def test_too_few_cases_is_inconclusive():
    d = decide(policy(min_cases=100), run([1] * 40))
    assert d.status == "inconclusive" and by_rule(d)["minimum_cases"].status == "inconclusive"


def test_unfinished_run_is_incomplete_and_skipped_cases_are_named():
    r = run([1] * 40, status="incomplete")
    r.records.append(Record(case_id="s", status="skipped"))
    d = decide(policy(), r)
    assert d.status == "incomplete" and "did not run" in by_rule(d)["evaluation_complete"].observed


def test_evaluator_errors_make_the_run_incomplete():
    r = run([1] * 40)
    r.records.append(Record(case_id="e", passed=None, evaluator_error=True))
    assert decide(policy(), r).status == "incomplete"


def test_a_confirmed_failure_beats_incomplete():
    r = run([1] * 20 + [0] * 80, status="incomplete")
    assert decide(policy(overall=Rule(minimum_score=0.9)), r).status == "fail"


def test_dimension_rules_and_missing_dimension():
    cand = merge(run([1] * 100, "qa"), run([1] * 40 + [0] * 60, "safety", "s"))
    p = policy(dimensions={"qa": Rule(minimum_score=0.9), "safety": Rule(minimum_score=0.9), "ghost": Rule(minimum_score=0.5)})
    d = decide(p, cand)
    rules = by_rule(d)
    assert (rules["qa.minimum_score"].status, rules["safety.minimum_score"].status, rules["ghost.minimum_score"].status) == (
        "pass", "fail", "inconclusive")
    assert d.status == "fail"


def test_dimension_min_cases():
    d = decide(policy(dimensions={"qa": Rule(minimum_score=0.5, min_cases=500)}), run([1] * 100))
    assert by_rule(d)["qa.minimum_score"].status == "inconclusive"


def test_regression_fail_pass_and_inconclusive():
    base = run([1] * 90 + [0] * 10)
    p = policy(overall=Rule(maximum_regression=0.02))
    assert decide(p, run([1] * 60 + [0] * 40), base).status == "fail"
    # 100 identical cases show no change, but they cannot prove that a fall of 2 points is impossible. The gate says so.
    assert decide(p, run([1] * 90 + [0] * 10), base).status == "inconclusive"
    big = run([1] * 900 + [0] * 100)
    assert decide(p, run([1] * 900 + [0] * 100), big).status == "pass"
    small = decide(p, run([1] * 45 + [0] * 5), run([1] * 46 + [0] * 4))   # One case worse. Cannot tell.
    assert small.status == "inconclusive"


def test_regression_without_a_baseline_is_inconclusive():
    d = decide(policy(overall=Rule(maximum_regression=0.01)), run([1] * 100))
    assert d.status == "inconclusive" and by_rule(d)["regression"].observed == "no baseline"


def test_critical_failures():
    ok = run([1] * 40, metadata={"critical": True})
    assert decide(policy(critical_failures_allowed=0), ok).status == "pass"
    bad = run([1] * 39 + [0], metadata={"critical": True})
    d = decide(policy(critical_failures_allowed=0), bad)
    assert d.status == "fail" and "c39" in by_rule(d)["critical_failures"].evidence
    assert decide(policy(critical_failures_allowed=1), bad).status == "pass"
    none = decide(policy(critical_failures_allowed=0), run([1] * 40))
    assert none.status == "inconclusive" and "no critical" in by_rule(none)["critical_failures"].observed


def test_a_failed_call_on_a_critical_case_counts():
    r = run([1] * 39, metadata={"critical": True})
    r.records.append(Record(case_id="x", status="failed", metadata={"critical": True}))
    assert decide(policy(critical_failures_allowed=0), r).status == "fail"


def test_latency_gate():
    p = policy(latency_p95_max_seconds=1.0)
    assert decide(p, run([1] * 100, latency=200.0)).status == "pass"
    assert decide(p, run([1] * 100, latency=3000.0)).status == "fail"
    assert decide(p, run([1] * 10, latency=3000.0)).status == "inconclusive"   # Too few calls for a P95.
    rng = random.Random(0)
    mixed = run([1] * 100)
    for r in mixed.records:
        r.latency_ms = 500.0 if rng.random() < 0.9 else 1500.0
    assert by_rule(decide(p, mixed))["latency_p95"].status in ("inconclusive", "fail")


def test_cost_gate_and_unknown_cost():
    p = policy(cost_per_success_max_usd=0.01)
    assert decide(p, run([1] * 40, cost=0.005)).status == "pass"
    assert decide(p, run([1] * 40, cost=0.05)).status == "fail"
    d = decide(p, run([1] * 40))
    assert d.status == "inconclusive" and "TRUSTBENCH_PRICES" in by_rule(d)["cost_per_success"].evidence


def test_call_failure_rate_gate():
    p = policy(call_failure_rate_max=0.05)
    assert decide(p, run([1] * 400)).status == "pass"
    failing = run([1] * 100)
    failing.records += [Record(case_id=f"f{i}", status="failed") for i in range(40)]
    assert by_rule(decide(p, failing))["call_failure_rate"].status == "fail"


def test_decision_can_be_saved_as_json():
    d = decide(policy(overall=Rule(minimum_score=0.5)), run([1] * 50)).to_dict()
    assert d["status"] == "pass" and d["checks"][0]["rule"] == "evaluation_complete"
