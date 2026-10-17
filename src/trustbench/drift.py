"""Has the traffic or the behaviour of the system changed?

We compare a recent window (current) with an earlier window (reference), feature by feature:
  numbers      latency, text sizes, tokens, cost, number of steps      PSI + KS test
  categories   model, prompt version, error type                       PSI + chi-square test
  rates        errors, empty answers, bad user feedback, online checks z-test for two proportions
Because we test many features, Benjamini-Hochberg keeps false alarms low.

Drift is a signal to investigate. It does NOT prove that quality fell. New users may ask new things.
"""

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .db import ProductionTrace, TraceEvaluation, TraceSpan, utcnow
from .stats import benjamini_hochberg, chi2_homogeneity, ks_two_sample, mean, psi, psi_categorical, two_proportion_p

MIN_TRACES = 30
MAX_TRACES = 50000


@dataclass
class Finding:
    feature: str
    kind: str                     # number | category | rate
    statistic: float | None       # PSI for numbers and categories. The change in rate for rates.
    p_value: float | None
    drifted: bool                 # Moderate or large change that stays significant after the correction.
    level: str                    # none | moderate | large
    reference: str
    current: str


@dataclass
class DriftReport:
    reference_traces: int
    current_traces: int
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def drifted(self) -> list[Finding]:
        return [f for f in self.findings if f.drifted]


def _level(psi_value: float | None) -> str:
    return "none" if psi_value is None or psi_value < 0.1 else "moderate" if psi_value < 0.25 else "large"


def _load(factory: sessionmaker, workspace_id: str, start: dt.datetime, end: dt.datetime, name: str | None) -> dict:
    with factory() as s:
        q = select(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id, ProductionTrace.started_at >= start,
                                          ProductionTrace.started_at < end)
        if name:
            q = q.where(ProductionTrace.name == name)
        rows = list(s.scalars(q.order_by(ProductionTrace.started_at.desc()).limit(MAX_TRACES)))
        pks = [r.id for r in rows]
        spans: Counter = Counter()
        tools: Counter = Counter()
        evals: dict[str, list[bool]] = {}
        for i in range(0, len(pks), 500):
            chunk = pks[i:i + 500]
            for pk, kind, n in s.execute(select(TraceSpan.trace_pk, TraceSpan.kind, func.count()).where(TraceSpan.trace_pk.in_(chunk)).group_by(TraceSpan.trace_pk, TraceSpan.kind)):
                spans[pk] += n
                if kind == "tool":
                    tools[pk] += n
            for e in s.execute(select(TraceEvaluation.evaluator, TraceEvaluation.kind, TraceEvaluation.passed).where(TraceEvaluation.trace_pk.in_(chunk))):
                evals.setdefault(f"{e[0]} ({e[1]})", []).append(e[2])
    return {"rows": rows, "spans": spans, "tools": tools, "evals": evals}


def _numbers(d: dict) -> dict[str, list[float]]:
    rows = d["rows"]
    pick = lambda f: [float(getattr(r, f)) for r in rows if getattr(r, f) is not None]
    return {"latency (ms)": pick("duration_ms"), "input size (chars)": pick("input_chars"), "output size (chars)": pick("output_chars"),
            "input tokens": pick("input_tokens"), "output tokens": pick("output_tokens"), "cost (USD)": pick("cost_usd"),
            "steps per trace": [float(d["spans"][r.id]) for r in rows], "tool calls per trace": [float(d["tools"][r.id]) for r in rows]}


def _categories(d: dict) -> dict[str, list]:
    rows = d["rows"]
    return {"model": [r.model or "none" for r in rows], "prompt version": [r.prompt_version or "none" for r in rows],
            "error type": [r.error_type or "none" for r in rows]}


def _rates(d: dict) -> dict[str, tuple[int, int]]:
    rows = d["rows"]
    out = {"error rate": (sum(r.status == "error" for r in rows), len(rows)),
           "empty answer rate": (sum(r.output_chars == 0 for r in rows), sum(r.output_chars is not None for r in rows)),
           "bad feedback rate": (sum(r.feedback == -1 for r in rows), sum(r.feedback is not None for r in rows))}
    for name, passed in d["evals"].items():
        out[f"online check fails: {name}"] = (sum(not p for p in passed), len(passed))
    return out


def drift_report(factory: sessionmaker, workspace_id: str, current_days: float = 7, reference_days: float = 30,
                 name: str | None = None, now: dt.datetime | None = None, q: float = 0.05) -> DriftReport:
    """Compare the last current_days with the reference_days before them."""
    now = now or utcnow()
    cur_start = now - dt.timedelta(days=current_days)
    ref_start = cur_start - dt.timedelta(days=reference_days)
    ref, cur = _load(factory, workspace_id, ref_start, cur_start, name), _load(factory, workspace_id, cur_start, now, name)
    rep = DriftReport(len(ref["rows"]), len(cur["rows"]))
    for label, n in (("reference", rep.reference_traces), ("current", rep.current_traces)):
        if n < MIN_TRACES:
            rep.warnings.append(f"The {label} window has {n} trace(s). At least {MIN_TRACES} are needed, so no drift is reported.")
    if rep.warnings:
        return rep

    rows: list[tuple[Finding, float | None]] = []
    rn, cn = _numbers(ref), _numbers(cur)
    for feat, a in rn.items():
        b = cn[feat]
        if len(a) < MIN_TRACES or len(b) < MIN_TRACES or len(set(a + b)) < 2:
            continue
        p_s, ks = psi(a, b), ks_two_sample(a, b)
        rows.append((Finding(feat, "number", p_s, ks[1], False, _level(p_s), f"median {sorted(a)[len(a) // 2]:.4g}, mean {mean(a):.4g}",
                             f"median {sorted(b)[len(b) // 2]:.4g}, mean {mean(b):.4g}"), ks[1]))
    rc, cc = _categories(ref), _categories(cur)
    for feat, a in rc.items():
        b = cc[feat]
        if len(set(a + b)) < 2:
            continue
        chi = chi2_homogeneity(Counter(a), Counter(b))
        p_s = psi_categorical(a, b)
        top = lambda xs: ", ".join(f"{k} {v / len(xs):.0%}" for k, v in Counter(xs).most_common(3))
        rows.append((Finding(feat, "category", p_s, chi[1] if chi else None, False, _level(p_s), top(a), top(b)), chi[1] if chi else None))
    rr, cr = _rates(ref), _rates(cur)
    for feat, (k1, n1) in rr.items():
        k2, n2 = cr.get(feat, (0, 0))
        if n1 < MIN_TRACES or n2 < MIN_TRACES:
            continue
        p = two_proportion_p(k1, n1, k2, n2)
        change = k2 / n2 - k1 / n1
        # A rate changes by a number of points, so the level uses the size of the change as well as the p-value.
        level = "none" if abs(change) < 0.02 else "moderate" if abs(change) < 0.10 else "large"
        rows.append((Finding(feat, "rate", change, p, False, level, f"{k1 / n1:.1%} of {n1}", f"{k2 / n2:.1%} of {n2}"), p))

    testable = [i for i, (_, p) in enumerate(rows) if p is not None]
    keep = benjamini_hochberg([rows[i][1] for i in testable], q)
    significant = {i for i, k in zip(testable, keep) if k}
    for i, (f, _) in enumerate(rows):
        f.drifted = i in significant and f.level != "none"
    rep.findings = sorted((f for f, _ in rows), key=lambda f: (not f.drifted, {"large": 0, "moderate": 1, "none": 2}[f.level], f.p_value if f.p_value is not None else 1))
    return rep
