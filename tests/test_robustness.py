import pytest

from trustbench.robustness import MIN_SLICE, robustness_report
from trustbench.rundata import Record


def pair(i, orig, var, transform="typos", task="qa"):
    return [Record(case_id=f"c{i}", task=task, passed=orig),
            Record(case_id=f"c{i}~{transform}", task=task, passed=var,
                   metadata={"source_case": f"c{i}", "transform": transform})]


def make(spec, transform="typos", task="qa", start=0):
    """spec: list of (orig, var) booleans."""
    return [r for i, (o, v) in enumerate(spec, start) for r in pair(i, o, v, transform, task)]


def test_four_numbers_with_a_known_answer():
    # 40 pairs: 20 pass/pass, 10 pass/fail, 5 fail/pass, 5 fail/fail.
    spec = [(1, 1)] * 20 + [(1, 0)] * 10 + [(0, 1)] * 5 + [(0, 0)] * 5
    r = robustness_report(make([(bool(o), bool(v)) for o, v in spec]))
    assert r.pairs == 40
    assert r.original_rate == 30 / 40 and r.variant_rate == 25 / 40
    assert r.degradation == pytest.approx(0.125) and r.degradation_ci[0] < 0.125 < r.degradation_ci[1]
    assert (r.break_rate.k, r.break_rate.n) == (10, 30) and r.break_rate.value == pytest.approx(1 / 3)
    assert (r.fix_rate.k, r.fix_rate.n) == (5, 10)
    assert r.consistency.k == 25 and r.consistently_wrong.k == 5
    assert r.break_rate.ci[0] < 1 / 3 < r.break_rate.ci[1]


def test_consistent_and_consistently_wrong():
    r = robustness_report(make([(False, False)] * 20))
    assert r.consistency.value == 1.0 and r.consistently_wrong.value == 1.0 and r.degradation == 0
    assert r.break_rate.n == 0 and r.break_rate.value is None   # No passing originals to break.


def test_a_perfect_model_has_no_breaks():
    r = robustness_report(make([(True, True)] * 50))
    assert r.break_rate.value == 0.0 and r.consistency.value == 1.0 and r.fragile_cases == {}


def test_worst_transform_and_task_need_enough_pairs():
    recs = (make([(True, True)] * 15, "whitespace", "qa", 0) + make([(True, False)] * 12 + [(True, True)] * 3, "typos", "math", 100)
            + make([(True, False)] * 3, "formatting", "qa", 200))
    r = robustness_report(recs)
    assert r.worst_transform.name == "typos" and r.worst_transform.variant_rate == pytest.approx(0.2)
    assert r.worst_task.name == "math"
    assert "formatting" in [g.name for g in r.by_transform]       # Listed, but too small to name as the worst.
    assert next(g for g in r.by_transform if g.name == "formatting").pairs < MIN_SLICE


def test_fragile_cases_list_the_transforms_that_broke_them():
    recs = make([(True, False)], "typos", "qa", 0) + pair(0, True, False, "whitespace")[1:]
    recs[-1] = Record(case_id="c0~whitespace", passed=False, metadata={"source_case": "c0", "transform": "whitespace"})
    assert robustness_report(recs).fragile_cases == {"c0": ["typos", "whitespace"]}


def test_failed_calls_and_unscored_cases():
    recs = make([(True, True)] * 10)
    recs.append(Record(case_id="c99", passed=True))
    recs.append(Record(case_id="c99~typos", status="failed", metadata={"source_case": "c99", "transform": "typos"}))
    recs.append(Record(case_id="c98", passed=True))
    recs.append(Record(case_id="c98~typos", status="skipped", metadata={"source_case": "c98", "transform": "typos"}))
    r = robustness_report(recs)
    assert r.pairs == 11 and r.break_rate.k == 1    # A failed call on the variant counts as a break. The report leaves out a skipped one.


def test_no_pairs_gives_a_clear_warning():
    r = robustness_report([Record(case_id="a", passed=True)])
    assert r.pairs == 0 and "robustness build" in r.warnings[0]


def test_small_runs_warn():
    assert any("Only 6 pairs" in w for w in robustness_report(make([(True, False)] * 6)).warnings)
