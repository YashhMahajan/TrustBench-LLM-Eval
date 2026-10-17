"""Does the statistics code do what it claims? We test it on data where we know the truth.

Part 1 injects known failures into a model and checks that TrustBench sees them.
Part 2 simulates many experiments and checks error rates.
"""

import random

import pytest

from trustbench import report
from trustbench.comparison import compare_records, compare_runs
from trustbench.datasets import CaseSpec, import_dataset
from trustbench.engine import RunOptions, run_eval
from trustbench.gate import evaluate_gate
from trustbench.policy import ReleasePolicy, Rule
from trustbench.providers import MockProvider
from trustbench.rundata import Record, load_run
from trustbench.slices import regression_scan

TASKS = ["math", "qa", "chat", "code"]


@pytest.fixture
def big_version(factory):
    cases = [CaseSpec(id=f"{t}{i}", task=t, input=f"{t} item <{i}> end", expected={"answer": "ok"},
                      metadata={"critical": t == "qa" and i < 5}) for t in TASKS for i in range(40)]
    with factory() as s:
        return import_dataset(s, "validation", cases)


def model(break_task=None, break_every=1, break_all_critical=False):
    """A mock that always answers 'ok', except where we break it on purpose."""
    class Broken(MockProvider):
        async def generate(self, req):
            if break_task and f"{break_task} item" in req.prompt and int(req.prompt.split("<")[1].split(">")[0]) % break_every == 0:
                self.answers = {"item": "wrong"}
            else:
                self.answers = {"item": "ok"}
            if break_all_critical and "qa item <0>" in req.prompt:
                self.answers = {"item": "wrong"}
            return await super().generate(req)
    return Broken()


def run(factory, version, provider):
    return load_run(factory, run_eval(factory, version.id, ["mock:m"], RunOptions(backoff=0),
                                      provider_factory=lambda n: provider))


# ---- Part 1: injected failures -------------------------------------------------------

def test_a_broken_task_is_found_by_comparison_slices_and_gate(factory, big_version):
    base = run(factory, big_version, model())
    cand = run(factory, big_version, model(break_task="math", break_every=2))   # Half of the math cases break.
    cmp = compare_runs(base, cand)
    assert cmp.overall.verdict == "meaningful_regression" and cmp.by_task["math"].verdict == "meaningful_regression"
    assert cmp.by_task["qa"].diff == 0 and cmp.by_task["qa"].verdict == "equivalent"
    hits = [s for s in regression_scan(base.records, cand.records) if s.regression]
    assert [(s.dimension, s.value) for s in hits][0] == ("task", "math")
    policy = ReleasePolicy(min_cases=100, critical_failures_allowed=0, overall=Rule(maximum_regression=0.02),
                           dimensions={"math": Rule(minimum_score=0.9)})
    d = evaluate_gate(policy, cand, baseline=base)
    assert d.status == "fail" and not d.allowed
    failed = {c.rule for c in d.checks if c.status == "fail"}
    assert {"overall.maximum_regression", "math.minimum_score"} <= failed


def test_an_unchanged_candidate_passes_the_same_gate(factory, big_version):
    base = run(factory, big_version, model())
    cand = run(factory, big_version, model())
    policy = ReleasePolicy(min_cases=100, critical_failures_allowed=0, overall=Rule(minimum_score=0.9, maximum_regression=0.02))
    d = evaluate_gate(policy, cand, baseline=base)
    assert d.status == "pass" and d.allowed


def test_one_broken_critical_case_blocks_even_when_the_average_is_high(factory, big_version):
    cand = run(factory, big_version, model(break_all_critical=True))
    assert cand.records and sum(r.passed for r in cand.records) / len(cand.records) > 0.99
    d = evaluate_gate(ReleasePolicy(min_cases=100, critical_failures_allowed=0, overall=Rule(minimum_score=0.9)), cand)
    assert d.status == "fail"
    assert {c.rule for c in d.checks if c.status == "fail"} == {"critical_failures"}


def test_broken_model_calls_are_a_regression_and_a_failed_rate(factory, big_version):
    base = run(factory, big_version, model())
    down = run(factory, big_version, MockProvider(always_fail=True))
    assert compare_runs(base, down).overall.diff == -1.0
    d = evaluate_gate(ReleasePolicy(call_failure_rate_max=0.05, critical_failures_allowed=None, min_cases=1), down)
    assert d.status in ("fail", "incomplete") and any(c.rule == "call_failure_rate" and c.status == "fail" for c in d.checks)


def test_a_small_run_cannot_pass_a_strict_gate(factory):
    cases = [CaseSpec(id=f"s{i}", input=f"item <{i}>", expected={"answer": "ok"}) for i in range(12)]
    with factory() as s:
        v = import_dataset(s, "small", cases)
    cand = run(factory, v, model())          # 12 of 12 correct. The estimate is 100%.
    d = evaluate_gate(ReleasePolicy(min_cases=12, critical_failures_allowed=None, overall=Rule(minimum_score=0.95)), cand)
    assert d.status == "inconclusive" and not d.allowed   # 12 cases cannot prove 95%.


def test_rescoring_with_a_stricter_evaluator_shows_as_a_regression_in_the_same_run(factory, big_version, monkeypatch):
    from trustbench.engine import rescore
    from trustbench.evaluators import EVALUATORS, Outcome
    rid = run_eval(factory, big_version.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n: model())
    monkeypatch.setitem(EVALUATORS, "exact_match", lambda ctx: Outcome("exact_match", "2", "qa" not in ctx.input["prompt"], 0.0, "x"))
    rescore(factory, rid)
    old, new = load_run(factory, rid, batch=0), load_run(factory, rid, batch=1)
    assert compare_records(old.records, new.records).by_task["qa"].verdict == "meaningful_regression"


# ---- Part 2: simulations -------------------------------------------------------------

def simulate_pair(rng, n, p_base, p_fix, p_break):
    """Baseline passes with p_base. The candidate fixes a failure with p_fix and breaks a pass with p_break."""
    base, cand = [], []
    for i in range(n):
        b = rng.random() < p_base
        c = (b and rng.random() >= p_break) or (not b and rng.random() < p_fix)
        base.append(Record(case_id=f"c{i}", passed=b))
        cand.append(Record(case_id=f"c{i}", passed=c))
    return base, cand


def test_interval_covers_the_true_difference_about_95_percent_of_the_time():
    rng = random.Random(5)
    p_base, p_fix, p_break = 0.7, 0.2, 0.05
    true_diff = (1 - p_base) * p_fix - p_base * p_break    # 0.06 - 0.035 = 0.025
    hits = 0
    runs = 150
    for _ in range(runs):
        base, cand = simulate_pair(rng, 150, p_base, p_fix, p_break)
        lo, hi = compare_records(base, cand, n_boot=300).overall.ci
        hits += lo <= true_diff <= hi
    assert 0.90 <= hits / runs <= 0.995


def test_equal_systems_are_rarely_called_different_and_a_real_drop_is_usually_found():
    rng = random.Random(9)
    false_alarms = detected = 0
    runs = 150
    for _ in range(runs):
        base = [Record(case_id=f"c{i}", passed=rng.random() < 0.8) for i in range(150)]
        same_skill = [Record(case_id=f"c{i}", passed=rng.random() < 0.8) for i in range(150)]   # Same skill, other luck.
        false_alarms += compare_records(base, same_skill, n_boot=200).overall.p_value < 0.05
        base, worse = simulate_pair(rng, 150, 0.8, 0.0, 0.15)           # The candidate loses 12 points.
        detected += compare_records(base, worse, n_boot=200).overall.verdict in ("meaningful_regression",)
    assert false_alarms / runs <= 0.09       # The expected rate is 5 percent. Allow for chance in 150 trials.
    assert detected / runs >= 0.9


def test_two_systems_with_the_same_skill_but_different_luck_rarely_fail_the_gate():
    rng = random.Random(21)
    policy = ReleasePolicy(min_cases=10, critical_failures_allowed=None, overall=Rule(maximum_regression=0.03))
    failures = 0
    runs = 100
    for _ in range(runs):
        base = [Record(case_id=f"c{i}", passed=rng.random() < 0.85) for i in range(200)]
        cand = [Record(case_id=f"c{i}", passed=rng.random() < 0.85) for i in range(200)]
        from trustbench.rundata import RunData
        d = evaluate_gate(policy, RunData("c", "complete", "d", "h", 0.0, cand), baseline=RunData("b", "complete", "d", "h", 0.0, base))
        failures += d.status == "fail"
    assert failures / runs <= 0.05
