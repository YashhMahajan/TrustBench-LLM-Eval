"""Read results back from the database."""

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .db import (
    EvaluationResult, EvaluationRun, ModelConfiguration, ModelResponse, TestCase, TestExecution,
)


def case_rows(factory: sessionmaker, run_id: str, batch: int | None = None) -> list[dict]:
    """One row for each execution. 'passed' is None when the model call failed.

    It reads the run with a fixed number of queries (see load_run), not one query for each execution.
    """
    from .rundata import load_run
    rows = []
    for r in load_run(factory, run_id, batch).records:
        rows.append({
            "model": r.model, "case_id": r.case_id, "status": r.status, "attempts": r.attempts, "output": r.output,
            "error": r.error, "latency_ms": r.latency_ms, "cost_usd": r.cost_usd,
            "passed": all(x["passed"] for x in r.results) if r.results else None,
            "rationale": "; ".join(x["rationale"] for x in r.results if not x["passed"]),
        })
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    """Per model: counts, pass rate and mean latency."""
    out: dict[str, dict] = {}
    for r in rows:
        m = out.setdefault(r["model"], {"model": r["model"], "total": 0, "scored": 0,
                                         "passed": 0, "failed_calls": 0, "skipped": 0,
                                         "latency": [], "cost": 0.0})
        m["total"] += 1
        if r["status"] == "skipped":
            m["skipped"] += 1
        elif r["status"] == "failed":
            m["failed_calls"] += 1
        elif r["passed"] is not None:
            m["scored"] += 1
            m["passed"] += bool(r["passed"])
            m["latency"].append(r["latency_ms"])
        m["cost"] += r["cost_usd"] or 0.0
    for m in out.values():
        lat = m.pop("latency")
        m["pass_rate"] = m["passed"] / m["scored"] if m["scored"] else None
        m["mean_latency_ms"] = sum(lat) / len(lat) if lat else None
    return list(out.values())


def get_run(factory: sessionmaker, run_id: str) -> EvaluationRun | None:
    with factory() as s:
        return s.get(EvaluationRun, run_id)


def case_detail(factory: sessionmaker, run_id: str, case_id: str, batch: int | None = None) -> list[dict]:
    """Everything about one test case: the answer of each model and why each evaluator scored it so."""
    with factory() as s:
        if batch is None:
            batch = s.scalar(
                select(func.max(EvaluationResult.batch))
                .join(ModelResponse, ModelResponse.id == EvaluationResult.response_id)
                .join(TestExecution, TestExecution.id == ModelResponse.execution_id)
                .where(TestExecution.run_id == run_id)) or 0
        execs = s.execute(
            select(TestExecution, TestCase, ModelConfiguration)
            .join(TestCase, TestCase.id == TestExecution.test_case_id)
            .join(ModelConfiguration, ModelConfiguration.id == TestExecution.model_config_id)
            .where(TestExecution.run_id == run_id, TestCase.case_id == case_id)
            .order_by(ModelConfiguration.id, TestExecution.repetition)).all()
        out = []
        for ex, case, mc in execs:
            attempts = s.scalars(select(ModelResponse).where(ModelResponse.execution_id == ex.id)
                                 .order_by(ModelResponse.attempt)).all()
            last = attempts[-1] if attempts else None
            results = s.scalars(select(EvaluationResult).where(
                EvaluationResult.response_id == last.id, EvaluationResult.batch == batch)).all() if last and last.ok else []
            out.append({
                "model": f"{mc.provider}:{mc.model}", "repetition": ex.repetition, "status": ex.status,
                "input": case.input, "expected": case.expected,
                "output": last.output if last else None, "error": last.error if last and not last.ok else None,
                "attempts": [{"attempt": a.attempt, "ok": a.ok, "error": a.error} for a in attempts],
                "results": [{"evaluator": r.evaluator, "rubric_version": r.rubric_version, "passed": r.passed,
                             "score": r.score, "rationale": r.rationale, "details": r.details} for r in results],
            })
        return out
