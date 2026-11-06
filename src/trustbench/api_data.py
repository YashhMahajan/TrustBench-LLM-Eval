"""Read access to evaluation results for the dashboard (and for any other client).

Every route needs a key. A key sees the runs, datasets and proposals of its own workspace only.
"""

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from . import __version__, proposals as prop
from .calibration import calibration_from_records
from .comparison import compare_runs
from .db import (
    Baseline, DatasetVersion, EvaluationRun, Experiment, ProposedCase, ReleaseDecision, TestCase, Workspace,
)
from .datasets import CaseSpec, render_prompt
from .edd.experiments import compare_runs_of
from .failure_report import analyze_failures
from .failures import classify
from .frontier import apply_constraints, candidates_from_runs, pareto
from .gate import evaluate_gate
from .operations import operations
from .overview import get_overview
from .policy import ReleasePolicy
from .quality import check as check_dataset
from .rag import diagnose
from .report import case_detail
from .robustness import robustness_report
from .rundata import load_run
from .scorecard import scorecard
from .slices import find_slices, regression_scan
from .summary import run_summary
from .tenancy import Principal, owned


def _iso(t):
    return t.isoformat() + "Z" if t else None


def _plain(x):
    """Dataclasses, tuples and nested values to plain JSON-ready data."""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(x) and not isinstance(x, type):
        return _plain(asdict(x))
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def register(app: FastAPI, need, db) -> None:
    def workspace(f: sessionmaker, p: Principal) -> Workspace:
        with f() as s:
            return s.get(Workspace, p.workspace_id)

    def own_run(f: sessionmaker, p: Principal, run_id: str) -> EvaluationRun:
        ws = workspace(f, p)
        with f() as s:
            run = s.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id, owned(EvaluationRun.workspace_id, ws)))
        if run is None:
            raise HTTPException(404, "No such run.")
        return run

    def records(f, p, run_id: str, model: str | None):
        own_run(f, p, run_id)
        rd = load_run(f, run_id)
        try:
            return rd, rd.for_model(model)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/v1/me")
    def me(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        ws = workspace(f, p)
        return {"workspace": {"id": ws.id, "name": ws.name}, "role": p.role, "version": __version__}

    # ---- Runs ---------------------------------------------------------------------------

    @app.get("/v1/runs")
    def runs(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), limit: int = Query(30, ge=1, le=200)):
        ws = workspace(f, p)
        with f() as s:
            rows = list(s.execute(select(EvaluationRun, DatasetVersion).join(DatasetVersion, DatasetVersion.id == EvaluationRun.dataset_version_id)
                                  .where(owned(EvaluationRun.workspace_id, ws)).order_by(EvaluationRun.created_at.desc()).limit(limit)))
            baseline_of = {b.run_id: b.name for b in s.scalars(select(Baseline).where(owned(Baseline.workspace_id, ws)))}
        out = []
        for run, version in rows:
            models = get_overview(f, run)
            out.append({"id": run.id, "name": run.name, "status": run.status, "created_at": _iso(run.created_at), "finished_at": _iso(run.finished_at),
                        "dataset": version.name, "dataset_version": version.content_hash, "cases": version.case_count, "models": models,
                        "cost_usd": run.total_cost_usd, "judge_cost_usd": run.judge_cost_usd, "baseline": baseline_of.get(run.id)})
        return {"runs": out}

    @app.get("/v1/runs/{run_id}")
    def run_detail(run_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        run = own_run(f, p, run_id)
        data = run_summary(load_run(f, run_id))
        data.update(name=run.name, created_at=_iso(run.created_at), finished_at=_iso(run.finished_at), software_version=run.software_version,
                    config=run.config_snapshot, cost_usd=run.total_cost_usd)
        return _plain(data)

    @app.get("/v1/runs/{run_id}/report")
    def run_report(run_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None,
                   format: str = Query("md", pattern="^(md|json)$")):
        own_run(f, p, run_id)
        from fastapi.responses import PlainTextResponse
        from .report_export import build_report, to_markdown
        try:
            data = build_report(f, run_id, model)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if format == "json":
            return data
        return PlainTextResponse(to_markdown(data), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="trustbench-run-{run_id}.md"'})

    @app.get("/v1/runs/{run_id}/cases")
    def run_cases(run_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None,
                  result: str | None = Query(None, pattern="^(pass|fail|error)$"), task: str | None = None, q: str | None = None,
                  limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        rd, recs = records(f, p, run_id, model)
        rows = []
        for r in recs:
            labels = classify(r)
            state = "error" if r.status == "failed" or r.evaluator_error else "skipped" if r.status != "done" else "pass" if r.passed else "fail"
            if result and state != result or task and r.task != task or q and q.lower() not in (r.case_id + " " + (r.output or "") + " " + render_prompt(r.input or {})).lower():
                continue
            rows.append({"case_id": r.case_id, "model": r.model, "repetition": r.repetition, "task": r.task, "result": state, "labels": labels,
                         "latency_ms": r.latency_ms, "cost_usd": r.cost_usd, "attempts": r.attempts, "output": (r.output or "")[:200],
                         "question": render_prompt(r.input or {})[:200]})
        order = {"fail": 0, "error": 1, "skipped": 2, "pass": 3}
        rows.sort(key=lambda x: (order[x["result"]], x["case_id"]))
        return {"total": len(rows), "cases": rows[offset:offset + limit], "tasks": sorted({r.task for r in recs}), "models": rd.models}

    @app.get("/v1/runs/{run_id}/cases/{case_id}")
    def run_case(run_id: str, case_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None):
        rd, recs = records(f, p, run_id, model)
        mine = [r for r in recs if r.case_id == case_id]
        if not mine:
            raise HTTPException(404, "No such case.")
        detail = [d for d in case_detail(f, run_id, case_id) if d["model"] == mine[0].model]
        for d, r in zip(detail, mine):
            d["labels"], d["rag"] = classify(r), _plain(diagnose(r))
        return _plain({"case_id": case_id, "task": mine[0].task, "metadata": mine[0].metadata, "executions": detail})

    @app.get("/v1/runs/{run_id}/failures")
    def run_failures(run_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None):
        return _plain(analyze_failures(records(f, p, run_id, model)[1]))

    @app.get("/v1/runs/{run_id}/robustness")
    def run_robustness(run_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None):
        return _plain(robustness_report(records(f, p, run_id, model)[1]))

    @app.get("/v1/runs/{run_id}/calibration")
    def run_calibration(run_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None):
        return _plain(calibration_from_records(records(f, p, run_id, model)[1]))

    @app.get("/v1/runs/{run_id}/slices")
    def run_slices(run_id: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None):
        return {"slices": [{"dimension": s.dimension, "value": s.value, "n": s.score.n, "rate": s.score.rate, "ci": s.score.ci,
                            "rest_rate": s.rest_rate, "p_value": s.p_value, "weak": s.weak, "underpowered": s.underpowered}
                           for s in find_slices(records(f, p, run_id, model)[1])]}

    # ---- Compare, gate, baselines -------------------------------------------------------

    def resolve(f, p, ref: str):
        ws = workspace(f, p)
        with f() as s:
            b = s.scalar(select(Baseline).where(Baseline.name == ref, owned(Baseline.workspace_id, ws)))
        run_id, model = (b.run_id, b.model) if b else (ref, None)
        own_run(f, p, run_id)
        return run_id, model

    @app.get("/v1/compare")
    def compare(candidate: str, baseline: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), model: str | None = None,
                baseline_model: str | None = None, min_effect: float = Query(0.01, ge=0, le=1)):
        (c_id, c_model), (b_id, b_model) = resolve(f, p, candidate), resolve(f, p, baseline)
        cand, base = load_run(f, c_id), load_run(f, b_id)
        c_model, b_model = model or c_model, baseline_model or b_model
        try:
            cmp = compare_runs(base, cand, b_model, c_model, min_effect)
            weak = [s for s in regression_scan(base.for_model(b_model), cand.for_model(c_model)) if s.regression]
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _plain({"candidate": {"run": c_id, "model": cand.pick_model(c_model)}, "baseline": {"run": b_id, "model": base.pick_model(b_model)},
                       "min_effect": min_effect, "comparison": cmp, "worse_slices": weak,
                       "operations": {"baseline": operations(base.for_model(b_model)), "candidate": operations(cand.for_model(c_model))}})

    @app.post("/v1/gate/preview")
    async def gate_preview(request: Request, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        """Check a run against a policy and show the result. Nothing is saved."""
        try:
            body = await request.json()
            run_id, model = resolve(f, p, body["run"])
            base = resolve(f, p, body["baseline"]) if body.get("baseline") else None
            policy = ReleasePolicy(**(body.get("policy") or {}))
            decision = evaluate_gate(policy, load_run(f, run_id), body.get("model") or model, load_run(f, base[0]) if base else None, base[1] if base else None)
        except HTTPException:
            raise
        except KeyError:
            raise HTTPException(400, 'The body needs {"run": "..."} and may have "baseline", "model" and "policy".')
        except (ValidationError, ValueError, TypeError) as e:    # The caller's policy or ids are wrong. Say how.
            raise HTTPException(422, str(e)[:300])
        return {"policy": policy.model_dump(), "decision": decision.to_dict()}

    @app.get("/v1/experiments")
    def get_experiments(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), limit: int = Query(40, ge=1, le=200)):
        """The history of variants, oldest first, each with its pass rate and, if it has a parent, the verdict on its hypothesis."""
        ws = workspace(f, p)
        with f() as s:
            rows = list(s.scalars(select(Experiment).where(owned(Experiment.workspace_id, ws)).order_by(Experiment.id.desc()).limit(limit)))[::-1]
            by_id = {e.id: e for e in s.scalars(select(Experiment).where(owned(Experiment.workspace_id, ws)))}
        out = []
        for e in rows:
            parent = by_id.get(e.parent_id)
            card = scorecard(load_run(f, e.run_id).for_model(e.model)).overall
            item = {"name": e.name, "parent": parent.name if parent else None, "run_id": e.run_id, "model": e.model, "hypothesis": e.hypothesis,
                    "created_at": _iso(e.created_at), "n": card.n, "rate": card.rate, "ci": card.ci, "verdict": None, "change": None, "ci_change": None,
                    "regressed_slices": []}
            if parent:
                rep = compare_runs_of(f, e.name, parent.name, e.hypothesis, e.run_id, parent.run_id, e.model, parent.model)
                item.update(verdict=rep.verdict, change=rep.comparison.overall.diff, ci_change=rep.comparison.overall.ci,
                            regressed_slices=[f"{x.dimension} = {x.value}" for x in rep.regressed_slices])
            out.append(item)
        return _plain({"experiments": out})

    @app.get("/v1/baselines")
    def get_baselines(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        ws = workspace(f, p)
        with f() as s:
            return {"baselines": [{"name": b.name, "run_id": b.run_id, "model": b.model, "set_at": _iso(b.created_at)}
                                  for b in s.scalars(select(Baseline).where(owned(Baseline.workspace_id, ws)).order_by(Baseline.name))]}

    @app.get("/v1/decisions")
    def get_decisions(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), run_id: str | None = None, limit: int = Query(50, ge=1, le=200)):
        ws = workspace(f, p)
        with f() as s:
            q = (select(ReleaseDecision).join(EvaluationRun, EvaluationRun.id == ReleaseDecision.run_id)
                 .where(owned(EvaluationRun.workspace_id, ws)).order_by(ReleaseDecision.id.desc()).limit(limit))
            if run_id:
                q = q.where(ReleaseDecision.run_id == run_id)
            return {"decisions": [{"id": d.id, "run_id": d.run_id, "model": d.model, "baseline_run_id": d.baseline_run_id, "policy": d.policy_name,
                                   "status": d.status, "allowed": d.allowed, "checks": d.checks, "warnings": d.warnings, "at": _iso(d.created_at)}
                                  for d in s.scalars(q)]}

    @app.get("/v1/frontier")
    def get_frontier(runs: str, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), min_quality: float | None = None,
                     max_cost: float | None = None, max_p95: float | None = None):
        ids = [r for r in runs.split(",") if r]
        for i in ids:
            own_run(f, p, i)
        cands = candidates_from_runs([load_run(f, i) for i in ids])
        pareto(cands)
        if any(x is not None for x in (min_quality, max_cost, max_p95)):
            apply_constraints(cands, min_quality, max_cost, max_p95)
        return {"candidates": _plain(cands)}

    # ---- Datasets and proposals ---------------------------------------------------------

    @app.get("/v1/datasets")
    def datasets(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        ws = workspace(f, p)
        with f() as s:
            out = []
            for v in s.scalars(select(DatasetVersion).where(owned(DatasetVersion.workspace_id, ws)).order_by(DatasetVersion.created_at.desc())):
                tasks = dict(s.execute(select(TestCase.task, func.count()).where(TestCase.dataset_version_id == v.id).group_by(TestCase.task)).all())
                runs_n = s.scalar(select(func.count()).select_from(EvaluationRun).where(EvaluationRun.dataset_version_id == v.id))
                out.append({"id": v.id, "name": v.name, "version": v.content_hash, "cases": v.case_count, "tasks": tasks, "runs": runs_n, "created_at": _iso(v.created_at)})
        return {"datasets": out}

    @app.get("/v1/datasets/{version_id}")
    def dataset_detail(version_id: int, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        ws = workspace(f, p)
        with f() as s:
            v = s.scalar(select(DatasetVersion).where(DatasetVersion.id == version_id, owned(DatasetVersion.workspace_id, ws)))
            if v is None:
                raise HTTPException(404, "No such dataset version.")
            cases = [CaseSpec(id=c.case_id, task=c.task, input=c.input, expected=c.expected, evaluation=c.evaluation, metadata=c.meta)
                     for c in s.scalars(select(TestCase).where(TestCase.dataset_version_id == v.id).order_by(TestCase.id))]
        issues = check_dataset(cases)
        return {"id": v.id, "name": v.name, "version": v.content_hash, "cases": len(cases),
                "issues": [{"severity": i.severity, "code": i.code, "case_ids": i.case_ids, "message": i.message} for i in issues],
                "preview": [{"id": c.id, "task": c.task, "question": render_prompt(c.input)[:240], "evaluators": [e["type"] for e in c.evaluation.get("evaluators", [])] or ["exact_match"],
                             "metadata": c.metadata} for c in cases[:200]]}

    @app.get("/v1/proposals")
    def proposals_list(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), status: str | None = Query(None, pattern="^(proposed|approved|rejected)$")):
        ws = workspace(f, p)
        with f() as s:
            q = select(ProposedCase).join(EvaluationRun, EvaluationRun.id == ProposedCase.source_run_id, isouter=True).where(
                (ProposedCase.workspace_id == ws.id) | owned(EvaluationRun.workspace_id, ws) & ProposedCase.source_run_id.is_not(None))
            if status:
                q = q.where(ProposedCase.status == status)
            return {"proposals": [{"id": x.id, "dataset": x.dataset_name, "case_id": x.case_id, "method": x.method, "status": x.status, "answer_checked": x.expected_verified,
                                   "source_case": x.source_case_id, "note": x.note, "spec": x.spec} for x in s.scalars(q.order_by(ProposedCase.id.desc()))]}

    def own_proposal(f, p, pid: int):
        ws = workspace(f, p)
        with f() as s:
            x = s.get(ProposedCase, pid)
            run = s.get(EvaluationRun, x.source_run_id) if x and x.source_run_id else None
            ok = x and (x.workspace_id == ws.id or (run is not None and (run.workspace_id == ws.id or (run.workspace_id is None and ws.name == "default"))))
        if not ok:
            raise HTTPException(404, "No such proposal.")

    @app.post("/v1/proposals/{pid}/approve")
    async def proposal_approve(pid: int, request: Request, p: Principal = Depends(need("write")), f: sessionmaker = Depends(db)):
        own_proposal(f, p, pid)
        body = await request.json() if await request.body() else {}
        try:
            prop.approve(f, [pid], bool(body.get("checked_answers")), body.get("note", ""))
        except prop.ReviewError as e:
            raise HTTPException(409, str(e))
        return {"id": pid, "status": "approved"}

    @app.post("/v1/proposals/{pid}/reject")
    async def proposal_reject(pid: int, request: Request, p: Principal = Depends(need("write")), f: sessionmaker = Depends(db)):
        own_proposal(f, p, pid)
        body = await request.json() if await request.body() else {}
        prop.reject(f, [pid], body.get("note", ""))
        return {"id": pid, "status": "rejected"}
