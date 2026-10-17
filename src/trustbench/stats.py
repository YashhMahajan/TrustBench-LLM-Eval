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
