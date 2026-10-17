"""Do the statistics do what they claim? Each check simulates data with a known truth."""

import math

from ..calibration import auroc, brier_score, expected_calibration_error
from ..stats import (
    benjamini_hochberg, chi2_sf, fisher_exact_less, ks_two_sample, mcnemar_exact, paired_bootstrap, psi, wilson_interval,
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


@check("statistics", "Paired interval covers the true change", "coverage 90% to 99%")
def paired_coverage(ctx: Ctx):
    rng, trials = ctx.rng("paired"), ctx.n(120)
    p_base, p_fix, p_break = 0.7, 0.2, 0.05
    truth = (1 - p_base) * p_fix - p_base * p_break
    hit = 0
    for t in range(trials):
        a, b = [], []
        for _ in range(150):
            x = rng.random() < p_base
            y = (x and rng.random() >= p_break) or (not x and rng.random() < p_fix)
            a.append(float(x))
            b.append(float(y))
        _, lo, hi = paired_bootstrap(a, b, n_boot=300, seed=t)
        hit += lo <= truth <= hi
    c = hit / trials
    return 0.90 <= c <= 0.99, f"{c:.1%} of {trials} trials", f"true change {truth:+.3f}"


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
