"""The state of a production system in one summary.

Every number says how we got it. So a dashboard never shows a guess as a fact:
  measured     counted or timed (errors, latency, cost, tokens, ratings, rule checks)
  estimated    a judge model's opinion on a sample (groundedness, helpfulness). A judge can be wrong.
  unmeasured   nothing in production can tell us. We list these on purpose.
User ratings are a signal from the people who chose to rate. They are not a sample of all traffic.
"""

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .dataprotect import policy_of
from .db import ProductionTrace, TraceEvaluation, Workspace, utcnow
from .online import JUDGE_BASED
from .stats import mean, percentile, wilson_interval

# Quality dimensions and the online evaluators that can speak about them.
DIMENSIONS = {
    "groundedness": {"grounded_claims"},
    "format and rules": {"rule", "json_schema", "contains"},
    "helpfulness (judge opinion)": {"llm_judge"},
}


def _rate(k: int, n: int) -> dict:
    return {"value": k / n if n else None, "k": k, "n": n, "ci": wilson_interval(k, n)}


def monitor_summary(factory: sessionmaker, workspace_id: str, days: float = 7, name: str | None = None, now: dt.datetime | None = None) -> dict:
    now = now or utcnow()
    start = now - dt.timedelta(days=days)
    with factory() as s:
        policy = policy_of(s.get(Workspace, workspace_id))
        q = select(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id, ProductionTrace.started_at >= start, ProductionTrace.started_at < now)
        if name:
            q = q.where(ProductionTrace.name == name)
        rows = list(s.scalars(q))
        pks = [r.id for r in rows]
        evals: dict[str, list[tuple[bool, str]]] = defaultdict(list)
        for i in range(0, len(pks), 500):
            for e in s.scalars(select(TraceEvaluation).where(TraceEvaluation.trace_pk.in_(pks[i:i + 500]))):
                if not e.details.get("error"):
                    evals[e.evaluator].append((e.passed, e.kind))

    n = len(rows)
    out: dict = {"window_days": days, "traces": n, "feature": name, "measured": {}, "estimated": {}, "unmeasured": [], "daily": []}
    if n == 0:
        out["unmeasured"].append("everything: there are no traces in this window")
        return out

    lat = [r.duration_ms for r in rows]
    costs = [r.cost_usd for r in rows if r.cost_usd is not None]
    rated = [r for r in rows if r.feedback is not None]
    m = out["measured"]
    m["error_rate"] = _rate(sum(r.status == "error" for r in rows), n)
    m["latency_ms"] = {"p50": percentile(lat, 50), "p95": percentile(lat, 95), "p99": percentile(lat, 99), "n": n}
    m["cost_usd"] = {"total": sum(costs) if costs else None, "per_trace": mean(costs) if costs else None, "priced_share": len(costs) / n}
    m["tokens"] = {"input": sum(r.input_tokens or 0 for r in rows), "output": sum(r.output_tokens or 0 for r in rows)}
    m["empty_answer_rate"] = _rate(sum(r.output_chars == 0 for r in rows), sum(r.output_chars is not None for r in rows))
    m["user_feedback"] = {"bad": _rate(sum(r.feedback == -1 for r in rated), len(rated)), "rated_share": len(rated) / n,
                          "note": "Only users who chose to rate are counted. They do not stand for all traffic."}
    sampled = sum(r.sampled for r in rows)
    out["online_checks"] = {"sampled_share": sampled / n, "evaluated": sum(r.eval_state == "done" for r in rows),
                            "pending": sum(r.eval_state == "pending" for r in rows), "failed_to_evaluate": sum(r.eval_state == "error" for r in rows)}
    for ev, results in sorted(evals.items()):
        kind = "estimated" if ev in JUDGE_BASED else "measured"
        passed = sum(p for p, _ in results)
        out["estimated" if kind == "estimated" else "measured"][f"check:{ev}"] = {"pass_rate": _rate(passed, len(results)),
                                                                                  "coverage": len(results) / n}
    covered = set(evals)
    for dim, names in DIMENSIONS.items():
        if not names & covered:
            out["unmeasured"].append(f"{dim}: no online check for it has results" + ("" if policy.online_evaluators else " (no online evaluator is set)"))
    out["unmeasured"].append("answer correctness: production has no reference answers. User ratings are only a rough signal.")

    by_day: dict = defaultdict(list)
    for r in rows:
        by_day[r.started_at.date()].append(r)
    out["daily"] = [{"date": d.isoformat(), "traces": len(v), "error_rate": sum(x.status == "error" for x in v) / len(v),
                     "latency_p95_ms": percentile([x.duration_ms for x in v], 95)} for d, v in sorted(by_day.items())]
    return out
