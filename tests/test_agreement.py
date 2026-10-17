import pytest

from trustbench.agreement import cohens_kappa, krippendorff_alpha, percent_agreement, weighted_kappa
from trustbench.stats import wilson_interval


def pairs(yy, yn, ny, nn):
    a = ["y"] * (yy + yn) + ["n"] * (ny + nn)
    b = ["y"] * yy + ["n"] * yn + ["y"] * ny + ["n"] * nn
    return a, b


def test_kappa_known_example():
    # 50 items: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no. Kappa is 0.4.
    assert cohens_kappa(*pairs(20, 5, 10, 15)) == pytest.approx(0.4)


def test_kappa_perfect_chance_and_undefined():
    assert cohens_kappa(*pairs(10, 0, 0, 10)) == 1.0
    assert cohens_kappa(*pairs(5, 5, 5, 5)) == pytest.approx(0.0)
    assert cohens_kappa(["y", "y"], ["y", "y"]) is None
    assert cohens_kappa([], []) is None


def test_high_agreement_can_still_have_low_kappa():
    a, b = pairs(90, 5, 5, 0)  # 90% agree, but almost all labels are 'y'.
    assert percent_agreement(a, b) == 0.9 and cohens_kappa(a, b) < 0.0


def test_weighted_kappa_punishes_big_misses_more():
    human = [0, 1, 2, 3] * 5
    near = [0, 2, 2, 3] * 5   # One small miss in every four.
    far = [0, 3, 2, 3] * 5    # One bigger miss in every four.
    levels = [0, 1, 2, 3]
    assert weighted_kappa(human, human, levels) == 1.0
    assert weighted_kappa(human, near, levels) > weighted_kappa(human, far, levels)


def test_krippendorff_alpha_hand_calculation():
    # Units (1,1), (1,2), (2,2), (2,2). Alpha is 1 - 14/30 = 0.5333.
    assert krippendorff_alpha([[1, 1], [1, 2], [2, 2], [2, 2]]) == pytest.approx(0.5333, abs=1e-4)


def test_krippendorff_perfect_missing_values_and_undefined():
    assert krippendorff_alpha([[1, 1], [2, 2], [3, None, 3]], level="interval") == 1.0
    assert krippendorff_alpha([[1, 1], [1, 1]]) is None
    assert krippendorff_alpha([[1, None]]) is None


def test_wilson_interval():
    lo, hi = wilson_interval(8, 10)
    assert 0.49 < lo < 0.50 and 0.94 < hi < 0.95
    assert wilson_interval(0, 5)[0] == 0.0 and wilson_interval(5, 5)[1] == 1.0
    assert wilson_interval(0, 0) is None
