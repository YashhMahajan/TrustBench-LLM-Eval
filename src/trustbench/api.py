"""The HTTP API. Send traces here, read them back, and manage the data policy.

Every call (except /v1/health) needs an API key: Authorization: Bearer tb_...
A key sees only its own workspace. Roles: read < write < admin.
"""

import datetime as dt
import json

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from . import __version__, audit, traces as tr
from .cli_common import get_factory
from .crypto import DecryptionError, SecretKeyMissing
from .dataprotect import PolicyProblem, DataPolicy, policy_of, update_policy
from .db import Workspace
from .tenancy import Principal, authenticate

MAX_BODY_BYTES = 5 * 1024 * 1024


def _iso(t: dt.datetime | None) -> str | None:
    return t.isoformat() + "Z" if t else None


def trace_json(row, spans=None) -> dict:
    d = {"trace_id": row.trace_id, "name": row.name, "started_at": _iso(row.started_at), "duration_ms": row.duration_ms,
         "status": row.status, "error_type": row.error_type, "model": row.model, "prompt_version": row.prompt_version,
         "input_tokens": row.input_tokens, "output_tokens": row.output_tokens, "cost_usd": row.cost_usd,
         "input_chars": row.input_chars, "output_chars": row.output_chars, "redactions": row.redactions,
         "truncated": row.truncated, "tags": row.tags, "feedback": row.feedback, "sampled": row.sampled,
         "eval_state": row.eval_state, "expires_at": _iso(row.expires_at), "has_content": bool(row.input_enc or row.output_enc)}
    if spans is not None:
        d["spans"] = [{"span_id": s.span_id, "parent_id": s.parent_id, "kind": s.kind, "name": s.name, "offset_ms": s.offset_ms,
                       "duration_ms": s.duration_ms, "status": s.status, "attrs": s.attrs} for s in spans]
    return d


def create_app(factory: sessionmaker | None = None) -> FastAPI:
    app = FastAPI(title="TrustBench", version=__version__, docs_url="/docs", redoc_url=None)
    app.state.factory = factory

    def db() -> sessionmaker:
        if app.state.factory is None:
            app.state.factory = get_factory()
        return app.state.factory

    def principal(request: Request, f: sessionmaker = Depends(db)) -> Principal:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else request.headers.get("x-api-key")
        p = authenticate(f, token)
        if p is None:     # The same answer for a missing, wrong or revoked key.
            raise HTTPException(401, "A valid API key is needed.", headers={"WWW-Authenticate": "Bearer"})
        return p

    def need(role: str):
        def check(p: Principal = Depends(principal)) -> Principal:
            if not p.allows(role):
                raise HTTPException(403, f"This key has the role '{p.role}'. The role '{role}' is needed.")
            return p
        return check

    @app.exception_handler(HTTPException)
    async def http_error(_, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.post("/v1/traces", status_code=202)
    async def post_traces(request: Request, p: Principal = Depends(need("write")), f: sessionmaker = Depends(db)):
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            raise HTTPException(413, f"The request is larger than {MAX_BODY_BYTES // 1024 // 1024} MB. Send smaller batches.")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, f"The request is larger than {MAX_BODY_BYTES // 1024 // 1024} MB. Send smaller batches.")
        try:
            raw = json.loads(body)
            items = raw["traces"]
            if not isinstance(items, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'The body must be JSON like {"traces": [...]}.')
        if len(items) > tr.MAX_BATCH:
            raise HTTPException(413, f"At most {tr.MAX_BATCH} traces in one request.")
        valid, rejected = [], []
        position = []     # The place in the request of each valid trace.
        for i, item in enumerate(items):
            try:
                valid.append(tr.TraceIn(**item))
                position.append(i)
            except (ValidationError, TypeError) as e:
                msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()) if isinstance(e, ValidationError) else "not an object"
                rejected.append({"index": i, "reason": msg[:300]})
        res = tr.ingest(f, p.workspace_id, valid)
        rejected += [{"index": position[i], "reason": reason} for i, reason in res.rejected]
        if res.eval_payloads:
            from .online import enqueue_for_evaluation
            enqueue_for_evaluation(f, p.workspace_id, res.eval_payloads)
        return {"accepted": res.accepted, "duplicates": res.duplicates, "rejected": sorted(rejected, key=lambda r: r["index"])}

    @app.get("/v1/traces")
    def get_traces(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db),
                   since: dt.datetime | None = None, until: dt.datetime | None = None, status: str | None = Query(None, pattern="^(ok|error)$"),
                   name: str | None = None, model: str | None = None, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        rows = tr.list_traces(f, p.workspace_id, since, until, status, name, model, limit, offset)
        return {"traces": [trace_json(r) for r in rows]}

    @app.get("/v1/traces/{trace_id}")
    def get_one(trace_id: str, reveal: bool = False, p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        found = tr.get_trace(f, p.workspace_id, trace_id)
        if found is None:
            raise HTTPException(404, "No such trace.")
        row, spans = found
        out = trace_json(row, spans)
        from .online import trace_evaluations
        out["evaluations"] = [{"evaluator": e.evaluator, "kind": e.kind, "passed": e.passed, "score": e.score, "rationale": e.rationale,
                               "rubric_version": e.rubric_version, "details": e.details} for e in trace_evaluations(f, p.workspace_id, row.id)]
        if reveal:
            if not p.allows("admin"):
                raise HTTPException(403, "Only an admin key can read stored content.")
            try:
                out["content"] = tr.reveal(row)
            except (SecretKeyMissing, DecryptionError) as e:
                raise HTTPException(409, str(e))
            audit.record(f, p, "trace.reveal", trace_id)
        return out

    @app.post("/v1/traces/{trace_id}/feedback")
    async def post_feedback(trace_id: str, request: Request, p: Principal = Depends(need("write")), f: sessionmaker = Depends(db)):
        try:
            value = (await request.json())["value"]
            ok = tr.set_feedback(f, p.workspace_id, trace_id, value)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'The body must be {"value": -1}, {"value": 0} or {"value": 1}.')
        if not ok:
            raise HTTPException(404, "No such trace.")
        return {"trace_id": trace_id, "feedback": value}

    @app.get("/v1/flagged")
    def get_flagged(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), name: str | None = None, limit: int = Query(100, ge=1, le=500)):
        from .production_tests import flagged_traces
        return {"flagged": [vars(x) for x in flagged_traces(f, p.workspace_id, name, limit)]}

    @app.post("/v1/traces/{trace_id}/promote", status_code=201)
    async def post_promote(trace_id: str, request: Request, p: Principal = Depends(need("admin")), f: sessionmaker = Depends(db)):
        from .production_tests import PromoteError, promote
        try:
            body = await request.json() if (await request.body()) else {}
            prop = promote(f, p.workspace_id, trace_id, body.get("answer"), body.get("dataset"))
        except PromoteError as e:
            raise HTTPException(409, str(e))
        except (ValueError, AttributeError):
            raise HTTPException(400, 'The body must be JSON like {"answer": "...", "dataset": "..."} or empty.')
        audit.record(f, p, "trace.promote", trace_id)
        return {"proposal_id": prop.id, "dataset": prop.dataset_name, "case_id": prop.case_id, "answer_checked": prop.expected_verified}

    @app.delete("/v1/traces/{trace_id}", status_code=204)
    def delete_one(trace_id: str, p: Principal = Depends(need("admin")), f: sessionmaker = Depends(db)):
        if not tr.delete_trace(f, p.workspace_id, trace_id):
            raise HTTPException(404, "No such trace.")
        audit.record(f, p, "trace.delete", trace_id)

    @app.get("/v1/settings")
    def get_settings(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db)):
        with f() as s:
            return policy_of(s.get(Workspace, p.workspace_id)).model_dump()

    @app.put("/v1/settings")
    async def put_settings(request: Request, p: Principal = Depends(need("admin")), f: sessionmaker = Depends(db)):
        try:
            changes = await request.json()
            if not isinstance(changes, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "The body must be a JSON object with the settings to change.")
        try:
            new = update_policy(f, p.workspace_id, **changes)
        except PolicyProblem as e:
            raise HTTPException(422, str(e))
        except TypeError:
            raise HTTPException(422, "Unknown setting.")
        audit.record(f, p, "settings.update", ",".join(sorted(changes)))
        return new.model_dump()

    @app.get("/v1/drift")
    def get_drift(p: Principal = Depends(need("read")), f: sessionmaker = Depends(db), current_days: float = Query(7, gt=0, le=365),
                  reference_days: float = Query(30, gt=0, le=365), name: str | None = None):
        from .drift import drift_report
        rep = drift_report(f, p.workspace_id, current_days, reference_days, name)
        return {"reference_traces": rep.reference_traces, "current_traces": rep.current_traces, "warnings": rep.warnings,
                "note": "Drift is a signal to investigate. It does not prove that quality fell.",
                "findings": [vars(x) for x in rep.findings]}

    @app.get("/v1/audit")
    def get_audit(p: Principal = Depends(need("admin")), f: sessionmaker = Depends(db)):
        return {"entries": [{"id": e.id, "key_id": e.key_id, "action": e.action, "target": e.target, "at": _iso(e.created_at)}
                            for e in audit.entries(f, p.workspace_id)]}

    return app
