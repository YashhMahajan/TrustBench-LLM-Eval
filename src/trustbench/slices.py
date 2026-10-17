"""Find where a model is weak. A single average hides weak groups of cases.

Each group (slice) is compared with all other cases. Because we test many
slices, some look weak by chance. Benjamini-Hochberg removes most of those.
"""

from dataclasses import dataclass

from .rundata import Record, case_outcomes
from .scorecard import Score, rate_score
from .stats import benjamini_hochberg, fisher_exact_less, mean, two_group_permutation_p

MIN_SLICE = 5   # Smaller slices are listed but not tested.


def length_bucket(words: int) -> str:
    return "short (under 20 words)" if words < 20 else "medium (20 to 99 words)" if words < 100 else "long (100 words or more)"


@dataclass
class Slice:
    dimension: str
    value: str
    score: Score
    rest_rate: float | None
    p_value: float | None        # One-sided: lower than the rest. None when the slice is too small.
    weak: bool                   # Still significant after Benjamini-Hochberg.
    underpowered: bool


def slice_keys(rec: Record) -> list[tuple[str, str]]:
    keys = [("task", rec.task), ("input length", length_bucket(rec.input_words))]
    keys += [(k, str(v)) for k, v in sorted(rec.metadata.items())
             if isinstance(v, (str, bool, int)) and k not in ("critical", "requires_judge")]
    return keys


def find_slices(records: list[Record], q: float = 0.05) -> list[Slice]:
    outcomes = {c: o for c, o in case_outcomes(records).items() if o is not None}
    first = {}
    for r in records:
        first.setdefault(r.case_id, r)
    members: dict[tuple[str, str], list[str]] = {}
    for cid in outcomes:
        for key in slice_keys(first[cid]):
            members.setdefault(key, []).append(cid)

    rows = []
    for (dim, value), ids in sorted(members.items()):
        if len(ids) == len(outcomes):
            continue   # A slice with every case says nothing new.
        inside = [outcomes[c] for c in ids]
        in_set = set(ids)
        rest = [o for c, o in outcomes.items() if c not in in_set]
        under = len(inside) < MIN_SLICE or len(rest) < MIN_SLICE
        p = None
        if not under:
            if all(x in (0.0, 1.0) for x in inside + rest):
                p = fisher_exact_less(int(sum(inside)), len(inside), int(sum(rest)), len(rest))
            else:
                p = two_group_permutation_p(inside, rest)
        rows.append([dim, value, rate_score(inside), mean(rest), p, under])

    tested = [i for i, r in enumerate(rows) if r[4] is not None]
    keep = benjamini_hochberg([rows[i][4] for i in tested], q)
    weak = {i for i, k in zip(tested, keep) if k and rows[i][2].rate < rows[i][3]}
    slices = [Slice(r[0], r[1], r[2], r[3], r[4], i in weak, r[5]) for i, r in enumerate(rows)]
    return sorted(slices, key=lambda s: (not s.weak, s.score.rate if s.score.rate is not None else 2))


@dataclass
class SliceDelta:
    dimension: str
    value: str
    n: int                        # Paired cases in the slice.
    baseline: float
    candidate: float
    diff: float                   # Candidate minus baseline.
    p_value: float | None         # One-sided: candidate is worse. None when the slice is too small.
    regression: bool              # Worse, and still significant after Benjamini-Hochberg.
    underpowered: bool


def regression_scan(baseline: list[Record], candidate: list[Record], q: float = 0.05) -> list[SliceDelta]:
    """Look for slices where the candidate got worse. Tests many slices, so it controls false alarms."""
    from .stats import mcnemar_exact, paired_permutation_p

    base, cand = case_outcomes(baseline), case_outcomes(candidate)
    shared = [c for c in base.keys() & cand.keys() if base[c] is not None and cand[c] is not None]
    first = {}
    for r in baseline + candidate:
        first.setdefault(r.case_id, r)
    members: dict[tuple[str, str], list[str]] = {}
    for cid in sorted(shared):
        for key in slice_keys(first[cid]):
            members.setdefault(key, []).append(cid)

    rows = []
    for (dim, value), ids in sorted(members.items()):
        a, b = [base[c] for c in ids], [cand[c] for c in ids]
        under = len(ids) < MIN_SLICE
        p = None
        if not under:
            if all(x in (0.0, 1.0) for x in a + b):
                p = mcnemar_exact(sum(x == 1 and y == 0 for x, y in zip(a, b)),
                                  sum(x == 0 and y == 1 for x, y in zip(a, b)), "b_worse")
            else:
                p = paired_permutation_p(a, b, "b_worse", n_perm=2000)
        rows.append([dim, value, len(ids), mean(a), mean(b), mean(b) - mean(a), p, under])

    tested = [i for i, r in enumerate(rows) if r[6] is not None]
    keep = benjamini_hochberg([rows[i][6] for i in tested], q)
    flagged = {i for i, k in zip(tested, keep) if k and rows[i][5] < 0}
    out = [SliceDelta(*r[:7], i in flagged, r[7]) for i, r in enumerate(rows)]
    return sorted(out, key=lambda s: (not s.regression, s.diff))
