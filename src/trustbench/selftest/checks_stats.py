"""Do the statistics do what they claim? Each check simulates data with a known truth."""

import math

from ..calibration import auroc, brier_score, expected_calibration_error
from ..edd.costmodel import CostModel, annual_cost, cost_report
from ..rundata import Record
from ..stats import (
    benjamini_hochberg, chi2_sf, fisher_exact_less, ks_two_sample, mcnemar_exact, paired_binary_ci, psi, wilson_interval,
)
from . import Ctx, check


@check("statistics", "Wilson interval covers the true rate", "mean coverage 93% to 98.5% over 9 settings of p and n")
def wilson_coverage(ctx: Ctx):
    rng, trials = ctx.rng("wilson"), ctx.n(600)
    covers, worst = [], (1.0, "")
    for p in (0.05, 0.5, 0.9):
        for n in (10, 50, 200):
            hit = 0
            for _ in range(trials):
                k = sum(rng.random() < p for _ in range(n))
                lo, hi = wilson_interval(k, n)
                hit += lo <= p <= hi
            c = hit / trials
            covers.append(c)
            worst = min(worst, (c, f"p={p}, n={n}"))
    mean = sum(covers) / len(covers)
    return 0.93 <= mean <= 0.985, f"mean {mean:.1%}", f"lowest {worst[0]:.1%} at {worst[1]}"


@check("statistics", "Paired interval covers the true change, also at the extremes", "mean coverage 92% to 99% over 3 settings, none below 88%")
def paired_coverage(ctx: Ctx):
    rng, trials = ctx.rng("paired"), ctx.n(500)
    # (share of cases the baseline passes, chance a failed case is fixed, chance a passed case breaks, number of cases)
    settings = [(0.7, 0.2, 0.05, 150), (0.9, 0.0, 0.9, 30), (0.5, 0.1, 0.1, 60)]
    covers = []
    for p_base, p_fix, p_break, n in settings:
        truth = (1 - p_base) * p_fix - p_base * p_break
        hit = 0
        for _ in range(trials):
            a, b = [], []
            for _ in range(n):
                x = rng.random() < p_base
                y = (x and rng.random() >= p_break) or (not x and rng.random() < p_fix)
                a.append(float(x))
                b.append(float(y))
            lo, hi = paired_binary_ci(a, b)
            hit += lo <= truth <= hi
        covers.append(hit / trials)
    return 0.92 <= sum(covers) / 3 <= 0.99 and min(covers) >= 0.88, "coverage " + ", ".join(f"{c:.1%}" for c in covers), "the second setting is extreme: 90% of the passed cases break"


@check("statistics", "Paired interval does not claim certainty from a few cases", "all 8 of 8 cases break: the interval must still be wide")
def paired_small_samples(ctx: Ctx):
    lo8, hi8 = paired_binary_ci([1.0] * 8, [0.0] * 8)
    lo20, hi20 = paired_binary_ci([1.0] * 20, [0.0] * 20)
    lo0, hi0 = paired_binary_ci([1.0] * 60, [1.0] * 60)
    ok = hi8 - lo8 >= 0.4 and hi8 > -0.9 and hi20 - lo20 >= 0.2 and lo0 < -0.02 and hi0 > 0.02
    return ok, f"8 of 8 break: {lo8:+.2f} to {hi8:+.2f}; 20 of 20: {lo20:+.2f} to {hi20:+.2f}; 60 identical cases: {lo0:+.2f} to {hi0:+.2f}", "a plain bootstrap gives zero width here, which would be false certainty"


@check("statistics", "McNemar test: two equal systems are rarely called different", "false alarm rate at most 6%")
def mcnemar_type1(ctx: Ctx):
    rng, trials = ctx.rng("mcnemar1"), ctx.n(1000)
    alarms = 0
    for _ in range(trials):
        a_only = b_only = 0
        for _ in range(100):
            x, y = rng.random() < 0.8, rng.random() < 0.8
            a_only += x and not y
            b_only += y and not x
        alarms += mcnemar_exact(a_only, b_only) < 0.05
    r = alarms / trials
    return r <= 0.06, f"{r:.1%} of {trials} trials", "the exact test is conservative, so it should sit below 5%"


@check("statistics", "McNemar test: a real 12 point drop is found", "found in at least 95% of trials")
def mcnemar_power(ctx: Ctx):
    rng, trials = ctx.rng("mcnemar2"), ctx.n(400)
    found = 0
    for _ in range(trials):
        a_only = b_only = 0
        for _ in range(150):
            x = rng.random() < 0.8
            y = x and rng.random() >= 0.15
            a_only += x and not y
            b_only += y and not x
        found += mcnemar_exact(a_only, b_only, "b_worse") < 0.05
    r = found / trials
    return r >= 0.95, f"{r:.1%} of {trials} trials", "150 paired cases, the candidate loses 15% of the cases it passed"


@check("statistics", "Benjamini-Hochberg controls the false discovery rate", "false discoveries at most 8% of all discoveries")
def bh_fdr(ctx: Ctx):
    rng, trials = ctx.rng("bh"), ctx.n(200)
    fd = disc = 0
    for _ in range(trials):
        ps, real = [], []
        for i in range(40):
            weak = i < 10                       # 10 of 40 slices are truly weaker.
            a = sum(rng.random() < (0.45 if weak else 0.8) for _ in range(40))
            b = sum(rng.random() < 0.8 for _ in range(120))
            ps.append(fisher_exact_less(a, 40, b, 120))
            real.append(weak)
        keep = benjamini_hochberg(ps, 0.05)
        disc += sum(keep)
        fd += sum(k and not r for k, r in zip(keep, real))
    r = fd / disc if disc else 0.0
    return r <= 0.08, f"{r:.1%} ({fd} of {disc})", "40 slices tested, 10 truly weaker. Without control, about 1 in 4 findings would be false"


@check("statistics", "Drift tests: same data is rarely flagged, a half-sigma shift is found", "false alarms 3% to 7.5%, detection at least 95%")
def drift_tests(ctx: Ctx):
    rng, trials = ctx.rng("drift"), ctx.n(500)
    alarms = found = psi_alarms = 0
    for _ in range(trials):
        a = [rng.gauss(0, 1) for _ in range(300)]
        alarms += ks_two_sample(a, [rng.gauss(0, 1) for _ in range(300)])[1] < 0.05
        found += ks_two_sample(a, [rng.gauss(0.5, 1) for _ in range(300)])[1] < 0.05
        psi_alarms += psi(a, [rng.gauss(0, 1) for _ in range(300)]) >= 0.1
    fa, det, pa = alarms / trials, found / trials, psi_alarms / trials
    return 0.03 <= fa <= 0.075 and det >= 0.95 and pa <= 0.10, f"KS false alarms {fa:.1%}, detection {det:.1%}, PSI above 0.1 on equal data {pa:.1%}", "n = 300 in each window"


@check("statistics", "Calibration metrics match hand calculations", "all values exact")
def calibration_known(ctx: Ctx):
    brier = brier_score([0.9, 0.9], [True, False])
    ece = expected_calibration_error([0.9] * 100, [True] * 50 + [False] * 50)
    a1, a2 = auroc([0.9, 0.8, 0.3, 0.2], [True, True, False, False]), auroc([0.5, 0.5], [True, False])
    ok = math.isclose(brier, 0.41) and math.isclose(ece, 0.4) and a1 == 1.0 and a2 == 0.5
    return ok, f"Brier {brier:.2f}, ECE {ece:.2f}, AUROC {a1:.1f} and {a2:.1f}", "expected 0.41, 0.40, 1.0 and 0.5"


@check("statistics", "Chi-square tail matches the published table", "within 0.0005")
def chi2_table(ctx: Ctx):
    table = [(3.841, 1, 0.05), (5.991, 2, 0.05), (11.070, 5, 0.05), (18.307, 10, 0.05), (6.635, 1, 0.01), (9.210, 2, 0.01)]
    worst = max(abs(chi2_sf(x, df) - p) for x, df, p in table)
    return worst <= 5e-4, f"largest error {worst:.5f}", "six values from the standard table"


def _cost_model(**kw) -> CostModel:
    base = dict(volume_per_year=1_000_000, positive_rate=0.05, costs=dict(false_positive=2, false_negative=30, true_positive=2))
    return CostModel(**{**base, **kw})


@check("statistics", "Cost model matches hand calculations", "manual $380,000 and perfect $100,000 exactly, and 3 more hand-worked cases")
def cost_hand_calculation(ctx: Ctx):
    m = _cost_model()
    small = _cost_model(volume_per_year=1000, positive_rate=0.5, costs=dict(false_positive=1, false_negative=10, true_positive=100, true_negative=1000))
    cases = [(annual_cost(m, 0.02, 0.03, 0.20), 380_000), (annual_cost(m, 0.0, 0.0, 0.0), 100_000),
             (annual_cost(small, 1, 0, 0), 500 * 100 + 500 * 1), (annual_cost(small, 0, 1, 0), 500 * 10 + 500 * 1000),
             (annual_cost(small, 0, 0, 0.5), 500 * 100 + 500 * 1000 + 500)]
    wrong = [f"{got:,.0f} not {want:,.0f}" for got, want in cases if abs(got - want) > 1e-6]
    return not wrong, f"{len(cases) - len(wrong)} of {len(cases)} right", "; ".join(wrong)


@check("statistics", "Cost interval covers the true yearly cost", "coverage 90% to 100% in each of 3 settings (the box of two intervals is conservative)")
def cost_interval_coverage(ctx: Ctx):
    rng, trials = ctx.rng("cost"), ctx.n(300)
    m = _cost_model(processing_cost_per_item=0.01)
    covers = []
    for fn, fp, n_pos, n_neg in [(0.04, 0.03, 80, 320), (0.20, 0.10, 40, 160), (0.01, 0.01, 200, 800)]:
        truth = annual_cost(m, fp, fn, 0.01)
        hit = 0
        for _ in range(trials):
            recs = ([Record(case_id=f"p{i}", passed=rng.random() >= fn, metadata={"positive": True}) for i in range(n_pos)]
                    + [Record(case_id=f"n{i}", passed=rng.random() >= fp, metadata={"positive": False}) for i in range(n_neg)])
            lo, hi = cost_report(m, recs).annual_ci
            hit += lo <= truth <= hi
        covers.append(hit / trials)
    return min(covers) >= 0.90, "coverage " + ", ".join(f"{c:.1%}" for c in covers), ""
