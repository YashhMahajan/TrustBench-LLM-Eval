"""How much do two raters agree? A rater can be a person or a judge model.

Plain percent agreement is misleading when one label is common. Kappa
removes the agreement we expect by chance.
"""

from collections import Counter
from typing import Hashable, Sequence


def percent_agreement(a: Sequence, b: Sequence) -> float | None:
    return sum(x == y for x, y in zip(a, b)) / len(a) if a else None


def cohens_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float | None:
    """For labels without order. Returns None when kappa is undefined."""
    n = len(a)
    if n == 0 or len(b) != n:
        return None
    po = percent_agreement(a, b)
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def weighted_kappa(a: Sequence[int], b: Sequence[int], levels: Sequence[int], kind: str = "quadratic") -> float | None:
    """For ordered scores such as 0 to 3. A miss by 1 counts less than a miss by 3."""
    n, k = len(a), len(levels)
    if n == 0 or len(b) != n or k < 2:
        return None
    idx = {v: i for i, v in enumerate(levels)}
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1 / n
    ra = [sum(row) for row in obs]
    cb = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    def w(i, j):
        d = abs(i - j) / (k - 1)
        return d * d if kind == "quadratic" else d

    num = sum(w(i, j) * obs[i][j] for i in range(k) for j in range(k))
    den = sum(w(i, j) * ra[i] * cb[j] for i in range(k) for j in range(k))
    return None if den == 0 else 1 - num / den


def krippendorff_alpha(units: Sequence[Sequence], level: str = "nominal") -> float | None:
    """For two or more raters and missing labels. units[i] holds the labels for item i (None = missing).

    level is 'nominal' (no order) or 'interval' (numbers with equal steps).
    """
    coincidence: Counter = Counter()
    for labels in units:
        vals = [v for v in labels if v is not None]
        m = len(vals)
        if m < 2:
            continue
        for i, c in enumerate(vals):
            for j, k in enumerate(vals):
                if i != j:
                    coincidence[(c, k)] += 1 / (m - 1)
    n = sum(coincidence.values())
    if n <= 1:
        return None
    totals: Counter = Counter()
    for (c, _), v in coincidence.items():
        totals[c] += v

    def delta(c, k):
        return float(c != k) if level == "nominal" else float(c - k) ** 2

    observed = sum(v * delta(c, k) for (c, k), v in coincidence.items())
    expected = sum(totals[c] * totals[k] * delta(c, k) for c in totals for k in totals)
    return None if expected == 0 else 1 - (n - 1) * observed / expected
