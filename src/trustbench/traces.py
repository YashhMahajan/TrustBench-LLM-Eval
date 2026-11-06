"""Receive, store and read production traces.

A trace is one request to an AI system, with optional steps (spans) inside it.
Rules of this module:
  - The data policy of the workspace decides what the module keeps (see dataprotect).
  - If you send the same trace_id twice, the module stores it once.
  - A workspace can reach only its own traces.
"""

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from . import crypto
from .bulk import insert_many
from .dataprotect import DataPolicy, policy_of, protect
from .db import ProductionTrace, TraceSpan, Workspace, utcnow
from .providers import estimate_cost
from .redact import redact

MAX_BATCH = 500
MAX_SPANS = 200
Scalar = str | int | float | bool


class SpanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16], max_length=64)
    parent_id: str | None = Field(default=None, max_length=64)
    kind: Literal["model", "retrieval", "tool", "other"] = "other"
    name: str = Field(max_length=100)
    offset_ms: float = Field(default=0.0, ge=0)
    duration_ms: float = Field(default=0.0, ge=0)
    status: Literal["ok", "error"] = "ok"
    attrs: dict[str, Scalar] = {}

    @field_validator("attrs")
    @classmethod
    def _small(cls, v):
        if len(v) > 20:
            raise ValueError("A span can have at most 20 attributes.")
        return v


class TraceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=64)
    name: str = Field(default="default", min_length=1, max_length=100)
    started_at: dt.datetime | None = None
    duration_ms: float = Field(default=0.0, ge=0)
    status: Literal["ok", "error"] = "ok"
    error_type: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=100)
    input: str | None = None
    output: str | None = None
    context: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    tags: dict[str, Scalar] = {}
    feedback: int | None = None
    spans: list[SpanIn] = []

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        if len(v) > 20:
            raise ValueError("A trace can have at most 20 tags.")
        return v

    @field_validator("feedback")
    @classmethod
    def _feedback(cls, v):
        if v is not None and v not in (-1, 0, 1):
            raise ValueError("feedback must be -1, 0 or 1.")
        return v

    @field_validator("spans")
    @classmethod
    def _spans(cls, v):
        if len(v) > MAX_SPANS:
            raise ValueError(f"A trace can have at most {MAX_SPANS} spans.")
        return v


def _naive_utc(t: dt.datetime | None) -> dt.datetime:
    if t is None:
        return utcnow()
    if t.tzinfo is not None:
        t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t


def is_sampled(workspace_id: str, trace_id: str, rate: float) -> bool:
    """The same trace always gets the same answer, on every server. The function needs no random state."""
    h = int(hashlib.sha256(f"{workspace_id}:{trace_id}".encode()).hexdigest()[:8], 16) / 2 ** 32
    return h < rate


def _clean_scalars(d: dict[str, Scalar], policy: DataPolicy) -> dict[str, Scalar]:
    """Caller data can hold personal data too, so strings go through the redaction."""
    out = {}
    for k, v in d.items():
        out[str(k)[:100]] = redact(v[:500], policy.extra_redactions)[0] if isinstance(v, str) and policy.redact else (v[:500] if isinstance(v, str) else v)
    return out


@dataclass
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: list[tuple[int, str]] = field(default_factory=list)       # (position in the batch, reason)
    sampled: list[int] = field(default_factory=list)                     # Ids of the traces chosen for online evaluation.
    eval_payloads: dict[int, dict] = field(default_factory=dict)         # trace id -> redacted texts, in memory only.


def ingest(factory: sessionmaker, workspace_id: str, traces: list[TraceIn]) -> IngestResult:
    result = IngestResult()
    if len(traces) > MAX_BATCH:
        raise ValueError(f"A batch can have at most {MAX_BATCH} traces.")
    with factory.begin() as s:
        ws = s.get(Workspace, workspace_id)
        if ws is None:
            raise ValueError("Unknown workspace.")
        policy = policy_of(ws)
        ids = [t.trace_id for t in traces]
        existing = set(s.scalars(select(ProductionTrace.trace_id).where(
            ProductionTrace.workspace_id == workspace_id, ProductionTrace.trace_id.in_(ids))))
        seen: set[str] = set()
        built: list[tuple[ProductionTrace, TraceIn, dict]] = []
        for i, t in enumerate(traces):
            if t.trace_id in existing or t.trace_id in seen:
                result.duplicates += 1
                continue
            seen.add(t.trace_id)
            try:
                row, texts = _build_row(workspace_id, t, policy)
            except crypto.SecretKeyMissing as e:
                result.rejected.append((i, str(e)))
                continue
            built.append((row, t, texts))
        insert_many(s, ProductionTrace, (row for row, _, _ in built))
        ids = {}
        for i in range(0, len(built), 500):
            chunk = [r.trace_id for r, _, _ in built[i:i + 500]]
            ids.update(s.execute(select(ProductionTrace.trace_id, ProductionTrace.id).where(
                ProductionTrace.workspace_id == workspace_id, ProductionTrace.trace_id.in_(chunk))).all())
        for row, _, _ in built:
            row.id = ids[row.trace_id]
        insert_many(s, TraceSpan, (TraceSpan(trace_pk=row.id, workspace_id=workspace_id, span_id=sp.span_id, parent_id=sp.parent_id, kind=sp.kind,
                                             name=sp.name, offset_ms=sp.offset_ms, duration_ms=sp.duration_ms, status=sp.status,
                                             attrs=_clean_scalars(sp.attrs, policy)) for row, t, _ in built for sp in t.spans))
        for row, _, texts in built:
            result.accepted += 1
            if row.sampled:
                result.sampled.append(row.id)
                if policy.online_evaluators:
                    result.eval_payloads[row.id] = texts
    return result


def _build_row(workspace_id: str, t: TraceIn, policy: DataPolicy) -> tuple[ProductionTrace, dict[str, str | None]]:
    started = _naive_utc(t.started_at)
    row = ProductionTrace(
        workspace_id=workspace_id, trace_id=t.trace_id, name=t.name, started_at=started, duration_ms=t.duration_ms,
        status=t.status, error_type=t.error_type, model=t.model, prompt_version=t.prompt_version,
        input_tokens=t.input_tokens, output_tokens=t.output_tokens, tags=_clean_scalars(t.tags, policy),
        feedback=t.feedback, truncated=False, expires_at=started + dt.timedelta(days=policy.retention_days),
        sampled=is_sampled(workspace_id, t.trace_id, policy.sample_rate))
    row.eval_state = "pending" if row.sampled else "not_sampled"
    row.cost_usd = t.cost_usd if t.cost_usd is not None else estimate_cost(t.model or "", t.input_tokens, t.output_tokens)
    redactions: dict[str, int] = {}
    texts: dict[str, str | None] = {"input": None, "output": None, "context": None}
    for field_name, text in (("input", t.input), ("output", t.output), ("context", t.context)):
        if text is None:
            continue
        p = protect(policy, text)
        for k, n in p.redactions.items():
            redactions[k] = redactions.get(k, 0) + n
        row.truncated |= p.truncated
        texts[field_name] = p.clean
        if field_name == "input":
            row.input_hash, row.input_chars, row.input_enc = p.content_hash, p.length, p.ciphertext
        elif field_name == "output":
            row.output_hash, row.output_chars, row.output_enc = p.content_hash, p.length, p.ciphertext
        else:
            row.context_enc = p.ciphertext
    row.redactions = redactions
    return row, texts


# ---- Reading --------------------------------------------------------------------------

def list_traces(factory: sessionmaker, workspace_id: str, since: dt.datetime | None = None, until: dt.datetime | None = None,
                status: str | None = None, name: str | None = None, model: str | None = None,
                limit: int = 100, offset: int = 0) -> list[ProductionTrace]:
    with factory() as s:
        q = select(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id)
        if since:
            q = q.where(ProductionTrace.started_at >= _naive_utc(since))
        if until:
            q = q.where(ProductionTrace.started_at < _naive_utc(until))
        for col, val in ((ProductionTrace.status, status), (ProductionTrace.name, name), (ProductionTrace.model, model)):
            if val:
                q = q.where(col == val)
        return list(s.scalars(q.order_by(ProductionTrace.started_at.desc(), ProductionTrace.id.desc()).limit(min(limit, 1000)).offset(offset)))


def get_trace(factory: sessionmaker, workspace_id: str, trace_id: str) -> tuple[ProductionTrace, list[TraceSpan]] | None:
    with factory() as s:
        row = s.scalar(select(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id, ProductionTrace.trace_id == trace_id))
        if row is None:
            return None
        spans = list(s.scalars(select(TraceSpan).where(TraceSpan.trace_pk == row.id).order_by(TraceSpan.offset_ms, TraceSpan.id)))
        return row, spans


def reveal(row: ProductionTrace) -> dict[str, str | None]:
    """Decrypt the stored text. Callers must check that the user may see content."""
    dec = lambda x: crypto.decrypt_text(x) if x else None
    return {"input": dec(row.input_enc), "output": dec(row.output_enc), "context": dec(row.context_enc)}


def count_traces(factory: sessionmaker, workspace_id: str) -> int:
    with factory() as s:
        return s.scalar(select(func.count()).select_from(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id)) or 0


# ---- Deleting -------------------------------------------------------------------------

def _delete_where(s, condition) -> int:
    pks = list(s.scalars(select(ProductionTrace.id).where(condition)))
    if not pks:
        return 0
    s.execute(delete(TraceSpan).where(TraceSpan.trace_pk.in_(pks)))
    s.execute(delete(ProductionTrace).where(ProductionTrace.id.in_(pks)))
    return len(pks)


def purge_expired(factory: sessionmaker, now: dt.datetime | None = None) -> int:
    """Delete traces that are past their retention time, in all workspaces."""
    with factory.begin() as s:
        return _delete_where(s, ProductionTrace.expires_at < _naive_utc(now))


def delete_trace(factory: sessionmaker, workspace_id: str, trace_id: str) -> bool:
    with factory.begin() as s:
        return bool(_delete_where(s, (ProductionTrace.workspace_id == workspace_id) & (ProductionTrace.trace_id == trace_id)))


def set_feedback(factory: sessionmaker, workspace_id: str, trace_id: str, value: int) -> bool:
    """Record the opinion of a user about a trace. Returns False if the trace does not exist."""
    if value not in (-1, 0, 1):
        raise ValueError("feedback must be -1, 0 or 1.")
    with factory.begin() as s:
        row = s.scalar(select(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id, ProductionTrace.trace_id == trace_id))
        if row is None:
            return False
        row.feedback = value
        return True
