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
