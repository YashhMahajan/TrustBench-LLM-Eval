import pytest

from test_engine import ANSWERS, go
from trustbench.comparison import compare_records, compare_runs
from trustbench.providers import MockProvider
from trustbench.rundata import Record, load_run


def run_of(outcomes, task="qa", prefix="c"):
    return [Record(case_id=f"{prefix}{i}", task=task, passed=bool(o)) for i, o in enumerate(outcomes)]


def test_candidate_that_fixes_cases_is_a_clear_improvement():
    base = run_of([1] * 70 + [0] * 30)
    cand = run_of([1] * 70 + [0] * 20 + [1] * 10)
    c = compare_records(base, cand)
    d = c.overall
    assert (d.n, d.baseline, d.candidate, round(d.diff, 3)) == (100, 0.7, 0.8, 0.1)
    assert d.method == "mcnemar" and d.p_value == pytest.approx(2 / 1024, rel=0.01)
    assert (d.candidate_only_pass, d.baseline_only_pass) == (10, 0)
    assert d.verdict == "meaningful_improvement" and d.ci[0] > 0.04


def test_identical_runs_are_equivalent():
    r = run_of([1] * 80 + [0] * 120)
    d = compare_records(r, r, min_effect=0.02).overall
    assert d.diff == 0 and d.p_value == 1.0 and d.verdict == "equivalent"


def test_a_drop_is_a_regression():
    base = run_of([1] * 90 + [0] * 10)
    cand = run_of([1] * 70 + [0] * 30)
    assert compare_records(base, cand).overall.verdict == "meaningful_regression"


def test_small_runs_are_inconclusive_and_warn():
    base, cand = run_of([1, 1, 0, 1, 0]), run_of([1, 0, 0, 1, 1])
    c = compare_records(base, cand)
    assert c.overall.verdict == "inconclusive" and any("Only 5 paired" in w for w in c.warnings)


def test_pairing_by_case_id_leaves_out_unmatched_and_unscored_cases():
    base = run_of([1, 1, 0]) + [Record(case_id="only_base", passed=True)]
    cand = run_of([1, 0, 0]) + [Record(case_id="c2", repetition=1, status="skipped")] + [Record(case_id="only_cand", passed=True)]
    c = compare_records(base, cand)
    assert c.paired_cases == 3 and c.unpaired_cases == 2 and any("2 case(s)" in w for w in c.warnings)


def test_a_failed_call_counts_as_a_loss_for_the_candidate():
    base = run_of([1] * 40)
    cand = run_of([1] * 30) + [Record(case_id=f"c{30 + i}", status="failed") for i in range(10)]
    assert compare_records(base, cand).overall.diff == pytest.approx(-0.25)


def test_repetitions_use_the_permutation_test():
    base = [Record(case_id=f"c{i}", repetition=r, passed=True) for i in range(40) for r in range(2)]
    cand = [Record(case_id=f"c{i}", repetition=r, passed=(r == 0)) for i in range(40) for r in range(2)]
    d = compare_records(base, cand).overall
    assert d.method == "permutation" and d.diff == pytest.approx(-0.5) and d.verdict == "meaningful_regression"
    assert d.candidate_only_pass is None


def test_task_breakdown():
    base = run_of([1] * 40, "easy", "e") + run_of([1] * 40, "hard", "h")
    cand = run_of([1] * 40, "easy", "e") + run_of([1] * 20 + [0] * 20, "hard", "h")
    c = compare_records(base, cand)
    assert c.by_task["easy"].verdict == "equivalent" or c.by_task["easy"].diff == 0
    assert c.by_task["hard"].verdict == "meaningful_regression"
    assert c.overall.n == 80


def test_compare_two_models_in_one_run_and_dataset_warnings(factory, version):
    run_id = go(factory, version, MockProvider(ANSWERS), specs=("mock:m1", "mock:m2"))
    rd = load_run(factory, run_id)
    c = compare_runs(rd, rd, "mock:m1", "mock:m2")
    assert c.paired_cases == 3 and c.overall.diff == 0
    other = load_run(factory, go(factory, version, MockProvider(ANSWERS)))
    other.dataset_hash = "different"
    assert any("dataset versions differ" in w for w in compare_runs(rd, other, "mock:m1").warnings)
