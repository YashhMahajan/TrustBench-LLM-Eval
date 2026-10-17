"""Small statistics helpers. Phase 3 adds the comparison tests here."""

import math
import random


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% interval for a share. It stays inside 0..1 and works for small n."""
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)


def percentile(values, p: float) -> float | None:
    """The p-th percentile (0 to 100) with linear steps between values."""
    xs = sorted(values)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def bootstrap_ci(values, stat=mean, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float] | None:
    """Percentile bootstrap interval for any statistic. The seed makes it repeatable."""
    values = list(values)
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    stats = [stat(rng.choices(values, k=n)) for _ in range(n_boot)]
    return percentile(stats, 100 * alpha / 2), percentile(stats, 100 * (1 - alpha / 2))


def binomial_tail(n: int, k: int) -> float:
    """P(X >= k) for X ~ Binomial(n, 0.5)."""
    if k <= 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def mcnemar_exact(a_only: int, b_only: int, alternative: str = "two-sided") -> float:
    """Exact McNemar test for two systems that answered the same cases.

    a_only = cases A passed and B failed. b_only = cases B passed and A failed.
    Cases where both agree carry no information, so only these two counts matter.
    alternative: 'two-sided', 'b_worse' (A beats B) or 'b_better'.
    """
    n = a_only + b_only
    if n == 0:
        return 1.0
    if alternative == "b_worse":
        return binomial_tail(n, a_only)
    if alternative == "b_better":
        return binomial_tail(n, b_only)
    return min(1.0, 2 * binomial_tail(n, max(a_only, b_only)))


def paired_diffs(a, b) -> list[float]:
    """b minus a for each case. a and b are scores for the same cases in the same order."""
    if len(a) != len(b):
        raise ValueError("Paired scores need the same number of cases.")
    return [y - x for x, y in zip(a, b)]


def paired_bootstrap(a, b, n_boot: int = 4000, alpha: float = 0.05, seed: int = 0):
    """Mean difference (b minus a) and its interval. Whole cases are resampled, so the pairing stays."""
    d = paired_diffs(a, b)
    if not d:
        return None
    return mean(d), *bootstrap_ci(d, n_boot=n_boot, alpha=alpha, seed=seed)


def paired_permutation_p(a, b, alternative: str = "two-sided", n_perm: int = 10000, seed: int = 0) -> float:
    """Sign-flip test. If A and B were equal, each difference could have either sign.

    alternative: 'two-sided', 'b_worse' or 'b_better'.
    """
    d = paired_diffs(a, b)
    nonzero = [x for x in d if x != 0]
    if not nonzero:
        return 1.0
    observed = sum(nonzero)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(n_perm):
        s = sum(x if rng.random() < 0.5 else -x for x in nonzero)
        if alternative == "b_worse":
            extreme += s <= observed
        elif alternative == "b_better":
            extreme += s >= observed
        else:
            extreme += abs(s) >= abs(observed)
    return (extreme + 1) / (n_perm + 1)


VERDICTS = ("meaningful_improvement", "improvement_below_threshold", "equivalent",
            "inconclusive", "regression_below_threshold", "meaningful_regression")


def verdict(ci: tuple[float, float], min_effect: float) -> str:
    """Read a confidence interval for the change (candidate minus baseline).

    min_effect is the smallest change that matters in practice, for example 0.01.
      meaningful_*          the interval is clear of zero and past min_effect
      *_below_threshold     the interval is clear of zero but the change may be too small to matter
      equivalent            the interval is inside +/- min_effect: no change that matters
      inconclusive          we cannot tell. More cases are needed.
    """
    lo, hi = ci
    if lo > 0:
        return "meaningful_improvement" if lo >= min_effect else "improvement_below_threshold"
    if hi < 0:
        return "meaningful_regression" if hi <= -min_effect else "regression_below_threshold"
    if lo > -min_effect and hi < min_effect:
        return "equivalent"
    return "inconclusive"


def fisher_exact_less(a_pass: int, a_n: int, b_pass: int, b_n: int) -> float:
    """One-sided Fisher exact test: is the pass rate of group A lower than that of group B?"""
    total, passes = a_n + b_n, a_pass + b_pass
    lo = max(0, passes - b_n)
    denom = math.comb(total, passes)
    return min(1.0, sum(math.comb(a_n, k) * math.comb(b_n, passes - k) for k in range(lo, a_pass + 1)) / denom)


def benjamini_hochberg(p_values: list[float], q: float = 0.05) -> list[bool]:
    """Which p-values stay significant when we test many things? Controls the false discovery rate at q.

    If we test 100 slices, about 5 look bad by chance at p < 0.05. This removes most of them.
    """
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= q * rank / m:
            cutoff = rank
    keep = [False] * m
    for rank, i in enumerate(order, start=1):
        keep[i] = rank <= cutoff
    return keep


def two_group_permutation_p(group: list[float], rest: list[float], n_perm: int = 1000, seed: int = 0) -> float:
    """One-sided: is the mean of the group lower than the mean of the rest? Works for any scores."""
    if not group or not rest:
        return 1.0
    observed = mean(group) - mean(rest)
    pool, k = list(group) + list(rest), len(group)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        extreme += mean(pool[:k]) - mean(pool[k:]) <= observed
    return (extreme + 1) / (n_perm + 1)


# ---- Tests for a change in the distribution of data ---------------------------------------

def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def psi(reference: list[float], current: list[float], bins: int = 10) -> float | None:
    """Population Stability Index for numbers. Bins come from the quantiles of the reference.

    Below 0.1: no real change. 0.1 to 0.25: some change. Above 0.25: a large change.
    """
    if not reference or not current:
        return None
    edges = sorted({percentile(reference, 100 * i / bins) for i in range(1, bins)})
    def shares(xs: list[float]) -> list[float]:
        counts = [0] * (len(edges) + 1)
        for x in xs:
            counts[sum(x > e for e in edges)] += 1
        return [max(c / len(xs), 1e-4) for c in counts]
    return psi_from_shares(shares(reference), shares(current))


def psi_from_shares(ref: list[float], cur: list[float]) -> float:
    return sum((c - r) * math.log(c / r) for r, c in zip(ref, cur))


def psi_categorical(reference: list, current: list) -> float | None:
    if not reference or not current:
        return None
    cats = sorted(set(reference) | set(current), key=str)
    share = lambda xs, c: max(sum(x == c for x in xs) / len(xs), 1e-4)
    return psi_from_shares([share(reference, c) for c in cats], [share(current, c) for c in cats])


def ks_two_sample(a: list[float], b: list[float]) -> tuple[float, float] | None:
    """Kolmogorov-Smirnov test. Returns (D, p). D is the largest gap between the two cumulative distributions.
    The p-value uses the usual large-sample formula, so it is approximate for very small samples."""
    if not a or not b:
        return None
    xs, ys = sorted(a), sorted(b)
    i = j = 0
    d = 0.0
    while i < len(xs) and j < len(ys):
        v = min(xs[i], ys[j])
        while i < len(xs) and xs[i] <= v:
            i += 1
        while j < len(ys) and ys[j] <= v:
            j += 1
        d = max(d, abs(i / len(xs) - j / len(ys)))
    ne = len(xs) * len(ys) / (len(xs) + len(ys))
    lam = (math.sqrt(ne) + 0.12 + 0.11 / math.sqrt(ne)) * d
    if lam < 1e-9:
        return d, 1.0
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return d, min(1.0, max(0.0, p))


def _gamma_q(a: float, x: float) -> float:
    """Upper regularized incomplete gamma Q(a, x)."""
    if x <= 0:
        return 1.0
    if x < a + 1:   # Series for P, then Q = 1 - P.
        term = total = 1 / a
        for n in range(1, 500):
            term *= x / (a + n)
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return 1 - total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b, c, d = x + 1 - a, 1e300, 1 / (x + 1 - a)   # Continued fraction for Q.
    h = d
    for n in range(1, 500):
        an = -n * (n - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        h *= d * c
        if abs(d * c - 1) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x: float, df: int) -> float:
    """P(chi-square with df degrees of freedom > x)."""
    return _gamma_q(df / 2, x / 2)


def chi2_homogeneity(ref_counts: dict, cur_counts: dict) -> tuple[float, float, int] | None:
    """Do two samples have the same category shares? Returns (chi2, p, df). Categories are joined by key."""
    cats = sorted(set(ref_counts) | set(cur_counts), key=str)
    n1, n2 = sum(ref_counts.values()), sum(cur_counts.values())
    if len(cats) < 2 or n1 == 0 or n2 == 0:
        return None
    chi2 = 0.0
    for c in cats:
        a, b = ref_counts.get(c, 0), cur_counts.get(c, 0)
        total = a + b
        for obs, n in ((a, n1), (b, n2)):
            exp = total * n / (n1 + n2)
            chi2 += (obs - exp) ** 2 / exp
    df = len(cats) - 1
    return chi2, chi2_sf(chi2, df), df


def two_proportion_p(k1: int, n1: int, k2: int, n2: int) -> float | None:
    """Two-sided z-test: do the two samples have the same rate? Needs enough cases in each group."""
    if n1 == 0 or n2 == 0:
        return None
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (k2 / n2 - k1 / n1) / se
    return 2 * (1 - normal_cdf(abs(z)))
