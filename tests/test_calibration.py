import random

import pytest

from trustbench.calibration import (
    auroc, brier_score, calibration_report, expected_calibration_error, risk_coverage, selective_risk,
)


def test_brier_known_values():
    assert brier_score([0.9, 0.9], [True, False]) == pytest.approx(0.41)
    assert brier_score([1.0, 0.0], [True, False]) == 0.0
    assert brier_score([0.5, 0.5], [True, False]) == 0.25


def test_ece_is_small_when_calibrated_and_large_when_overconfident():
    rng = random.Random(0)
    conf = [0.8] * 1000
    right = [rng.random() < 0.8 for _ in conf]
    assert expected_calibration_error(conf, right) < 0.04
    assert expected_calibration_error([0.9] * 100, [True] * 50 + [False] * 50) == pytest.approx(0.4)


def test_auroc():
    assert auroc([0.9, 0.8, 0.3, 0.2], [True, True, False, False]) == 1.0
    assert auroc([0.2, 0.3, 0.8, 0.9], [True, True, False, False]) == 0.0
    assert auroc([0.5, 0.5], [True, False]) == 0.5       # Equal confidence: no information.
    assert auroc([0.5, 0.6], [True, True]) is None


def test_confidence_with_no_information_gives_auroc_near_half():
    rng = random.Random(2)
    conf = [rng.random() for _ in range(2000)]
    right = [rng.random() < 0.7 for _ in conf]
    assert 0.45 < auroc(conf, right) < 0.55


def test_risk_coverage_curve_and_selective_risk():
    conf, right = [0.9, 0.8, 0.7, 0.6], [True, True, False, True]
    assert risk_coverage(conf, right) == [(0.25, 0.0), (0.5, 0.0), (0.75, pytest.approx(1 / 3)), (1.0, 0.25)]
    assert selective_risk(conf, right, 0.5) == 0.0
    assert selective_risk(conf, right, 1.0) == 0.25
    assert selective_risk(conf, right, 0.1) is None


def test_equal_confidence_answers_enter_together():
    assert risk_coverage([0.5, 0.5], [True, False]) == [(1.0, 0.5)]


def test_report_collects_everything_and_warns_on_small_samples():
    r = calibration_report([0.9, 0.9, 0.6, 0.6], [True, True, False, True], unreadable=1)
    assert r.n == 4 and r.accuracy == 0.75 and r.mean_confidence == 0.75
    assert r.brier_ci[0] <= r.brier <= r.brier_ci[1] and r.auroc is not None
    assert any("Only 4 answers" in w for w in r.warnings) and any("1 answer(s)" in w for w in r.warnings)


def test_empty_report():
    r = calibration_report([], [])
    assert r.n == 0 and r.brier is None


def test_all_correct_warns_about_auroc():
    assert any("AUROC" in w for w in calibration_report([0.9] * 60, [True] * 60).warnings)
