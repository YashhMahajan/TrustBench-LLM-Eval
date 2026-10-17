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
