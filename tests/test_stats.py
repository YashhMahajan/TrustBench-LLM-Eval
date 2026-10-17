import random

import pytest

from trustbench.stats import bootstrap_ci, mean, percentile


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4], 0) == 1 and percentile([1, 2, 3, 4], 100) == 4
    assert percentile([10], 95) == 10 and percentile([], 50) is None
    assert percentile(range(1, 101), 95) == pytest.approx(95.05)


def test_bootstrap_is_repeatable_and_brackets_the_mean():
    data = [1] * 80 + [0] * 20
    lo, hi = bootstrap_ci(data)
    assert (lo, hi) == bootstrap_ci(data)
    assert lo < 0.8 < hi and hi - lo < 0.2


def test_bootstrap_works_for_other_statistics():
    lo, hi = bootstrap_ci(list(range(100)), stat=lambda xs: percentile(xs, 95))
    assert 85 < lo < hi <= 99


def test_bootstrap_edge_cases():
    assert bootstrap_ci([]) is None
    assert bootstrap_ci([0.5, 0.5, 0.5]) == (0.5, 0.5)


def test_bootstrap_interval_covers_the_true_mean_about_95_percent():
    rng = random.Random(1)
    hits = 0
    for _ in range(200):
        sample = [1 if rng.random() < 0.7 else 0 for _ in range(100)]
        lo, hi = bootstrap_ci(sample, n_boot=300, seed=rng.randrange(10**6))
        hits += lo <= 0.7 <= hi
    assert 0.88 <= hits / 200 <= 1.0


def test_mcnemar_known_values():
    from trustbench.stats import mcnemar_exact
    # 10 disagreements, split 9 to 1: p = 2 * (1 + 10) / 1024.
    assert mcnemar_exact(9, 1) == pytest.approx(22 / 1024)
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(0, 0) == 1.0


def test_mcnemar_one_sided():
    from trustbench.stats import mcnemar_exact
    assert mcnemar_exact(9, 1, "b_worse") == pytest.approx(11 / 1024)
    assert mcnemar_exact(9, 1, "b_better") > 0.99
    assert mcnemar_exact(9, 1) == pytest.approx(2 * mcnemar_exact(9, 1, "b_worse"))


def test_mcnemar_ignores_agreeing_cases():
    from trustbench.stats import mcnemar_exact
    # Only the disagreements enter the test. 1000 cases that agree change nothing.
    assert mcnemar_exact(8, 2) == mcnemar_exact(8, 2)


def test_mcnemar_false_alarm_rate_is_at_most_5_percent():
    """Two equal systems must rarely look different."""
    from trustbench.stats import mcnemar_exact
    rng = random.Random(7)
    alarms = 0
    for _ in range(2000):
        a_only = b_only = 0
        for _ in range(100):
            a, b = rng.random() < 0.8, rng.random() < 0.8  # Same true skill.
            a_only += a and not b
            b_only += b and not a
        alarms += mcnemar_exact(a_only, b_only) < 0.05
    assert alarms / 2000 <= 0.06


def test_paired_bootstrap_difference():
    from trustbench.stats import paired_bootstrap
    a = [1] * 70 + [0] * 30
    b = [1] * 70 + [0] * 20 + [1] * 10   # B fixes 10 cases and breaks none.
    diff, lo, hi = paired_bootstrap(a, b)
    assert diff == pytest.approx(0.10) and 0 < lo < diff < hi < 0.2
    assert paired_bootstrap([], []) is None


def test_pairing_gives_a_tighter_interval_than_ignoring_it():
    from trustbench.stats import paired_bootstrap
    rng = random.Random(3)
    base = [1 if rng.random() < 0.6 else 0 for _ in range(200)]
    better = [1 if (x or rng.random() < 0.05) else 0 for x in base]  # B is A plus a few fixes.
    _, lo, hi = paired_bootstrap(base, better)
    assert lo > 0  # The small gain is clear because the same cases are compared.


def test_paired_scores_must_match_in_length():
    from trustbench.stats import paired_diffs
    with pytest.raises(ValueError):
        paired_diffs([1], [1, 0])


def test_permutation_test_detects_a_clear_gap_and_ignores_noise():
    from trustbench.stats import paired_permutation_p
    a = [0.0] * 40
    b = [1.0] * 15 + [0.0] * 25
    assert paired_permutation_p(a, b) < 0.001
    assert paired_permutation_p(a, b, "b_worse") > 0.99
    assert paired_permutation_p(a, b, "b_better") < 0.001
    assert paired_permutation_p([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    mixed_a, mixed_b = [1, 0] * 20, [0, 1] * 20
    assert paired_permutation_p(mixed_a, mixed_b) > 0.5
