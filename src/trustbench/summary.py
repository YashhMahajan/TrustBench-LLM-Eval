"""One summary of a run, as plain data. The report command and the API both use it."""

from dataclasses import asdict

from .calibration import calibration_from_records
from .hallucination import ClaimMetrics, pool
from .operations import operations
from .rundata import Record, RunData
from .scorecard import scorecard
from .slices import find_slices


def hallucination_from_records(records: list[Record]) -> ClaimMetrics | None:
    """Pool the claim counts of all 'grounded_claims' results. None when there are none."""
    parts = []
    for rec in records:
        for res in rec.results:
            m = res["details"].get("metrics") if res["evaluator"] == "grounded_claims" else None
            if m:
                parts.append(ClaimMetrics(**{k: m[k] for k in (
                    "total", "supported", "contradicted", "insufficient", "not_applicable", "needs_review")}))
    return pool(parts) if parts else None


def _score(s) -> dict:
    return {"n": s.n, "rate": s.rate, "ci": s.ci, "method": s.method}


def model_summary(records: list[Record]) -> dict:
    card, ops = scorecard(records), operations(records)
    out = {
        "overall": _score(card.overall),
        "by_task": {t: _score(s) for t, s in card.by_task.items()},
        "left_out": {"skipped_cases": card.skipped_cases, "evaluator_errors": card.evaluator_errors},
        "operations": asdict(ops),
        "weak_slices": [{"dimension": s.dimension, "value": s.value, "n": s.score.n, "rate": s.score.rate,
                         "rest_rate": s.rest_rate, "p_value": s.p_value} for s in find_slices(records) if s.weak],
        "needs_human_review": sum(1 for r in records for res in r.results if res["details"].get("human_review")),
    }
    h = hallucination_from_records(records)
    if h:
        out["hallucination"] = h.to_dict()
    if any(res["evaluator"] == "calibrated_answer" for r in records for res in r.results):
        out["calibration"] = asdict(calibration_from_records(records))
    return out


def run_summary(rd: RunData) -> dict:
    return {"run_id": rd.run_id, "status": rd.status, "dataset": rd.dataset, "dataset_version": rd.dataset_hash,
            "judge_cost_usd": rd.judge_cost_usd,
            "models": {m: model_summary(rd.for_model(m)) for m in rd.models}}
