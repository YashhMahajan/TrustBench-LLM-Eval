import pytest

from trustbench.rundata import Record
from trustbench.scorecard import rate_score, scorecard


def recs(task, passes, fails, start=0):
    return ([Record(case_id=f"{task}{start + i}", task=task, passed=True) for i in range(passes)]
            + [Record(case_id=f"{task}{start + passes + i}", task=task, passed=False) for i in range(fails)])


def test_binary_outcomes_use_wilson_and_show_n():
    s = rate_score([1.0] * 8 + [0.0] * 2)
    assert (s.n, s.rate, s.method) == (10, 0.8, "wilson")
    assert s.ci[0] < 0.8 < s.ci[1]
    assert "n=10" in s.text and "80.0%" in s.text


def test_partial_scores_from_repetitions_use_the_bootstrap():
    s = rate_score([1.0, 0.5, 0.0, 1.0, 0.5] * 10)
    assert s.method == "bootstrap" and s.ci[0] < s.rate < s.ci[1]


def test_small_samples_have_wide_intervals():
    small, large = rate_score([1.0] * 9 + [0.0]), rate_score([1.0] * 900 + [0.0] * 100)
    assert (small.ci[1] - small.ci[0]) > 3 * (large.ci[1] - large.ci[0])


def test_empty_has_no_rate():
    s = rate_score([])
    assert s.rate is None and s.text == "-"


def test_scorecard_splits_by_task_and_counts_what_is_left_out():
    records = recs("qa", 6, 2) + recs("safety", 1, 1) + [
        Record(case_id="x", task="qa", status="skipped"),
        Record(case_id="y", task="qa", passed=None, evaluator_error=True)]
    card = scorecard(records)
    assert card.overall.n == 10 and card.overall.rate == pytest.approx(0.7)
    assert card.by_task["qa"].n == 8 and card.by_task["safety"].rate == 0.5
    assert (card.skipped_cases, card.evaluator_errors) == (2, 1)
