"""How stable is a model when the input changes in a way that should not matter?

We pair each variant with its original (metadata.source_case) and compare the results.
The four numbers are kept apart because they answer different questions:
  break rate      the model passed the original. How often does it fail the variant?
  degradation     how much lower is the pass rate on variants than on originals?
  consistency     how often do original and variant get the same result?
  worst slice     which transform or task is the weakest?
A model can be consistent and consistently wrong.
"""

from dataclasses import dataclass, field

from .rundata import Record, case_outcomes
from .stats import mean, paired_bootstrap, wilson_interval

MIN_SLICE = 10


@dataclass
class Rate:
    k: int
    n: int
    ci: tuple[float, float] | None

    @property
    def value(self) -> float | None:
        return self.k / self.n if self.n else None


def _rate(k: int, n: int) -> Rate:
    return Rate(k, n, wilson_interval(k, n))


@dataclass
class Group:
    name: str
    pairs: int
    original_rate: float
    variant_rate: float
    break_rate: Rate        # Among originals that passed.


@dataclass
class RobustnessReport:
    pairs: int
    original_rate: float | None = None
    variant_rate: float | None = None
    degradation: float | None = None           # Original minus variant. Positive means the variants are harder.
    degradation_ci: tuple[float, float] | None = None
    break_rate: Rate | None = None             # Passed the original, failed the variant.
    fix_rate: Rate | None = None               # Failed the original, passed the variant.
    consistency: Rate | None = None            # Same result for original and variant.
    consistently_wrong: Rate | None = None     # Failed both.
    by_transform: list[Group] = field(default_factory=list)
    by_task: list[Group] = field(default_factory=list)
    worst_transform: Group | None = None       # Lowest variant pass rate with enough pairs.
    worst_task: Group | None = None
    fragile_cases: dict[str, list[str]] = field(default_factory=dict)   # Original id -> transforms that broke it.
    warnings: list[str] = field(default_factory=list)


def _group(name: str, pairs: list[tuple[float, float]]) -> Group:
    passed = [(o, v) for o, v in pairs if o >= 0.5]
    broke = sum(1 for _, v in passed if v < 0.5)
    return Group(name, len(pairs), mean(o for o, _ in pairs), mean(v for _, v in pairs), _rate(broke, len(passed)))


def _worst(groups: list[Group]) -> Group | None:
    eligible = [g for g in groups if g.pairs >= MIN_SLICE]
    return min(eligible, key=lambda g: g.variant_rate) if eligible else None


def robustness_report(records: list[Record]) -> RobustnessReport:
    outcomes = case_outcomes(records)
    meta = {}
    for r in records:
        meta.setdefault(r.case_id, r)
    triples = []   # (original id, transform, task, original outcome, variant outcome)
    for cid, rec in meta.items():
        src = rec.metadata.get("source_case")
        if not src or src not in outcomes:
            continue
        o, v = outcomes[src], outcomes.get(cid)
        if o is not None and v is not None:
            triples.append((src, rec.metadata.get("transform", "?"), rec.task, o, v))
    rep = RobustnessReport(pairs=len(triples))
    if not triples:
        rep.warnings.append("No pairs of original and variant cases were scored. Build variants with 'trustbench robustness build'.")
        return rep

    orig, var = [t[3] for t in triples], [t[4] for t in triples]
    rep.original_rate, rep.variant_rate = mean(orig), mean(var)
    diff, lo, hi = paired_bootstrap(orig, var)
    rep.degradation, rep.degradation_ci = -diff, (-hi, -lo)
    po = [o >= 0.5 for o in orig]
    pv = [v >= 0.5 for v in var]
    rep.break_rate = _rate(sum(a and not b for a, b in zip(po, pv)), sum(po))
    rep.fix_rate = _rate(sum(b and not a for a, b in zip(po, pv)), len(po) - sum(po))
    rep.consistency = _rate(sum(a == b for a, b in zip(po, pv)), len(po))
    rep.consistently_wrong = _rate(sum(not a and not b for a, b in zip(po, pv)), len(po))

    for attr, idx in (("by_transform", 1), ("by_task", 2)):
        names = sorted({t[idx] for t in triples})
        setattr(rep, attr, [_group(n, [(t[3], t[4]) for t in triples if t[idx] == n]) for n in names])
    rep.worst_transform, rep.worst_task = _worst(rep.by_transform), _worst(rep.by_task)
    for src, transform, _, o, v in triples:
        if o >= 0.5 and v < 0.5:
            rep.fragile_cases.setdefault(src, []).append(transform)
    if rep.pairs < 30:
        rep.warnings.append(f"Only {rep.pairs} pairs. The rates are uncertain. See the intervals.")
    if rep.worst_transform is None and rep.by_transform:
        rep.warnings.append(f"No transform has {MIN_SLICE} pairs, so the worst transform is not named.")
    return rep
