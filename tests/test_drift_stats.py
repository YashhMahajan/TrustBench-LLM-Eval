import math
import random

import pytest

from trustbench.stats import (
    chi2_homogeneity, chi2_sf, ks_two_sample, normal_cdf, psi, psi_categorical, two_proportion_p,
)


def test_normal_cdf_known_values():
    assert normal_cdf(0) == 0.5
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)


@pytest.mark.parametrize("x,df,p", [(3.841, 1, 0.05), (5.991, 2, 0.05), (11.070, 5, 0.05), (18.307, 10, 0.05), (6.635, 1, 0.01), (0.0, 3, 1.0)])
def test_chi_square_tail_matches_the_tables(x, df, p):
    assert chi2_sf(x, df) == pytest.approx(p, abs=5e-4)


def test_chi_square_homogeneity():
    chi2, p, df = chi2_homogeneity({"a": 50, "b": 50}, {"a": 50, "b": 50})
    assert (chi2, p, df) == (0, 1.0, 1)
    chi2, p, df = chi2_homogeneity({"a": 90, "b": 10}, {"a": 50, "b": 50})
    assert p < 1e-6 and df == 1
    assert chi2_homogeneity({"a": 5}, {"a": 5}) is None and chi2_homogeneity({}, {"a": 1, "b": 2}) is None
    # A category that appears only in the new data counts.
    assert chi2_homogeneity({"a": 100}, {"a": 60, "new": 40})[1] < 1e-6


def test_ks_known_cases():
    same = ks_two_sample([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert same == (0.0, 1.0)
    d, p = ks_two_sample(list(range(100)), list(range(20, 120)))
    assert d == pytest.approx(0.2) and 0.02 < p < 0.045        # Table value for n = m = 100 and D = 0.2 is about 0.03.
    d, p = ks_two_sample([0.0] * 50, [1.0] * 50)
    assert d == 1.0 and p < 1e-10
    assert ks_two_sample([], [1]) is None


def test_ks_handles_ties():
    d, _ = ks_two_sample([1, 1, 1, 2], [1, 1, 2, 2])
    assert d == pytest.approx(0.25)


def test_ks_false_alarm_rate_is_near_5_percent():
    rng = random.Random(4)
    alarms = sum(ks_two_sample([rng.gauss(0, 1) for _ in range(150)], [rng.gauss(0, 1) for _ in range(150)])[1] < 0.05 for _ in range(1000))
    assert 0.02 < alarms / 1000 < 0.08


def test_psi_no_change_small_change_large_change():
    rng = random.Random(1)
    ref = [rng.gauss(0, 1) for _ in range(5000)]
    assert psi(ref, [rng.gauss(0, 1) for _ in range(5000)]) < 0.03
    assert 0.1 < psi(ref, [rng.gauss(0.5, 1) for _ in range(5000)]) < 0.4
    assert psi(ref, [rng.gauss(2, 1) for _ in range(5000)]) > 1.0
    assert psi([], [1]) is None


def test_psi_is_zero_for_identical_data_and_ignores_constant_columns():
    assert psi([1, 2, 3, 4] * 25, [1, 2, 3, 4] * 25) == pytest.approx(0, abs=1e-9)
    assert psi([5.0] * 50, [5.0] * 50) == pytest.approx(0, abs=1e-9)


def test_psi_categorical():
    assert psi_categorical(["a"] * 80 + ["b"] * 20, ["a"] * 80 + ["b"] * 20) == pytest.approx(0, abs=1e-9)
    assert psi_categorical(["a"] * 90 + ["b"] * 10, ["a"] * 50 + ["b"] * 50) > 0.25
    assert psi_categorical(["a"] * 100, ["a"] * 50 + ["new"] * 50) > 1          # A new category is a big change.


def test_two_proportions():
    assert two_proportion_p(50, 100, 50, 100) == pytest.approx(1.0)
    assert two_proportion_p(10, 100, 30, 100) < 0.001
    assert two_proportion_p(0, 50, 0, 50) == 1.0 and two_proportion_p(1, 0, 1, 1) is None
    assert two_proportion_p(40, 100, 52, 100) == pytest.approx(0.0886, abs=2e-3)      # z = 1.70
