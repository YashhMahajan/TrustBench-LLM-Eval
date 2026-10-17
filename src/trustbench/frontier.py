"""Quality, cost and latency together. The best model is rarely the best on all three.

A model is on the Pareto frontier when no other model is at least as good on every chosen
measure and better on one. Models on the frontier are the only sensible choices.
A model with an unknown value (for example, no price) cannot be compared on that measure,
so it is listed apart. We never treat an unknown cost as zero.
"""

from dataclasses import dataclass, field

from .operations import operations
from .rundata import RunData
from .scorecard import scorecard

OBJECTIVES = ("quality", "cost", "latency")   # quality: higher is better. cost and latency: lower is better.


@dataclass
class Candidate:
    name: str
    quality: float | None
    quality_ci: tuple[float, float] | None
    cases: int
    cost_per_success: float | None     # USD
    p95_seconds: float | None
    on_frontier: bool = False
    dominated_by: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)       # Measures we could not compare.
    feasible: bool | None = None
    why_not_feasible: list[str] = field(default_factory=list)
    quality_uncertain: bool = False    # Quality intervals overlap with a model that dominates this one.


def candidates_from_runs(runs: list[RunData]) -> list[Candidate]:
    multi = len(runs) > 1
    out = []
    for rd in runs:
        for model in rd.models:
            recs = rd.for_model(model)
            card, ops = scorecard(recs), operations(recs)
            p95 = ops.latency_ms[95]
            out.append(Candidate(f"{rd.run_id}:{model}" if multi else model, card.overall.rate, card.overall.ci, card.overall.n,
                                 ops.cost_per_success_usd, None if p95 is None else p95 / 1000))
    return out


def _value(c: Candidate, objective: str) -> float | None:
    return {"quality": c.quality, "cost": c.cost_per_success, "latency": c.p95_seconds}[objective]


def _better_or_equal(a: Candidate, b: Candidate, objective: str) -> bool:
    x, y = _value(a, objective), _value(b, objective)
    return x >= y if objective == "quality" else x <= y


def _strictly_better(a: Candidate, b: Candidate, objective: str) -> bool:
    x, y = _value(a, objective), _value(b, objective)
    return x > y if objective == "quality" else x < y


def pareto(cands: list[Candidate], objectives: tuple[str, ...] = OBJECTIVES) -> list[Candidate]:
    """Marks on_frontier and dominated_by on each candidate. Returns the candidates on the frontier."""
    for c in cands:
        c.unknown = [o for o in objectives if _value(c, o) is None]
        c.on_frontier, c.dominated_by, c.quality_uncertain = False, [], False
    known = [c for c in cands if not c.unknown]
    for c in known:
        for d in known:
            if d is c:
                continue
            if all(_better_or_equal(d, c, o) for o in objectives) and any(_strictly_better(d, c, o) for o in objectives):
                c.dominated_by.append(d.name)
                if "quality" in objectives and d.quality_ci and c.quality_ci and d.quality_ci[0] <= c.quality_ci[1] and d.quality > c.quality:
                    c.quality_uncertain = True
        c.on_frontier = not c.dominated_by
    return [c for c in known if c.on_frontier]


def apply_constraints(cands: list[Candidate], min_quality: float | None = None, max_cost: float | None = None,
                      max_p95_seconds: float | None = None, use_lower_bound: bool = True) -> list[Candidate]:
    """Marks feasible. A candidate is feasible only if every limit is met and known."""
    for c in cands:
        c.why_not_feasible = []
        if min_quality is not None:
            q = (c.quality_ci[0] if use_lower_bound and c.quality_ci else c.quality)
            if q is None:
                c.why_not_feasible.append("quality unknown")
            elif q < min_quality:
                c.why_not_feasible.append(f"quality {q:.1%}{' (low end)' if use_lower_bound else ''} is below {min_quality:.1%}")
        if max_cost is not None:
            if c.cost_per_success is None:
                c.why_not_feasible.append("cost unknown")
            elif c.cost_per_success > max_cost:
                c.why_not_feasible.append(f"cost ${c.cost_per_success:.4f} is above ${max_cost:.4f}")
        if max_p95_seconds is not None:
            if c.p95_seconds is None:
                c.why_not_feasible.append("latency unknown")
            elif c.p95_seconds > max_p95_seconds:
                c.why_not_feasible.append(f"P95 {c.p95_seconds:.2f} s is above {max_p95_seconds:.2f} s")
        c.feasible = not c.why_not_feasible
    return [c for c in cands if c.feasible]
