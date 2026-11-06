"""Database tables.

Raw model outputs (ModelResponse) and scores (EvaluationResult) live in
different tables. This lets us score old outputs again without a model call.
"""

import datetime as dt
import os

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    """The current time in UTC without a time zone. All our columns hold UTC without a zone,
    so the database cannot shift the value by its own time zone."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class DatasetVersion(Base):
    """An immutable snapshot. The hash comes from the content of the cases."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("name", "content_hash", "workspace_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    case_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class TestCase(Base):
    __test__ = False  # Tell pytest this is not a test class.
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("dataset_version_id", "case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(200))
    task: Mapped[str] = mapped_column(String(100))
    input: Mapped[dict] = mapped_column(Json)
    expected: Mapped[dict] = mapped_column(Json)
    evaluation: Mapped[dict] = mapped_column(Json)
    meta: Mapped[dict] = mapped_column(Json)


class ModelConfiguration(Base):
    __tablename__ = "model_configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    params: Mapped[dict] = mapped_column(Json)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    # Run lists are read for one workspace, newest first.
    __table_args__ = (Index("ix_evaluation_runs_workspace_created", "workspace_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"))
    # running | complete | incomplete
    status: Mapped[str] = mapped_column(String(20), default="running")
    config_snapshot: Mapped[dict] = mapped_column(Json)
    software_version: Mapped[str] = mapped_column(String(20))
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    judge_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    # Pass rate and interval for each model, stored when the run ends or is scored again.
    # The run list reads this and does not load the whole run. None means: not computed yet.
    overview: Mapped[dict | None] = mapped_column(Json, nullable=True)


class TestExecution(Base):
    """One test case, run on one model, for one repetition."""

    __test__ = False
    __tablename__ = "test_executions"
    # Resume and progress ask for the executions of one run in one status.
    __table_args__ = (Index("ix_test_executions_run_status", "run_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id"))
    model_config_id: Mapped[int] = mapped_column(ForeignKey("model_configurations.id"))
    repetition: Mapped[int] = mapped_column(Integer, default=0)
    # pending | done | failed | skipped
    status: Mapped[str] = mapped_column(String(20), default="pending")


class ModelResponse(Base):
    """One attempt. A retry makes a new row. We never overwrite a failure."""

    __tablename__ = "model_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey("test_executions.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_calls: Mapped[list | None] = mapped_column(Json, nullable=True)   # App targets only: what the app called.
    spans: Mapped[list | None] = mapped_column(Json, nullable=True)        # App targets only: timed steps.
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("model_responses.id"), index=True)
    # Batch 0 is the first scoring. Each re-score adds a new batch.
    batch: Mapped[int] = mapped_column(Integer, default=0)
    evaluator: Mapped[str] = mapped_column(String(100))
    rubric_version: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(Json, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class HumanAnnotation(Base):
    """A label from a person. We compare it with the judge to see if the judge is reliable."""

    __tablename__ = "human_annotations"
    __table_args__ = (UniqueConstraint("response_id", "annotator", "dimension"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("model_responses.id"), index=True)
    annotator: Mapped[str] = mapped_column(String(100))
    dimension: Mapped[str] = mapped_column(String(100))
    label: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Baseline(Base):
    """A named run to compare against, for example 'main'. Setting it again moves the name."""

    __tablename__ = "baselines"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    model: Mapped[str | None] = mapped_column(String(250), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ReleaseDecision(Base):
    """What the gate decided, with the policy and the evidence. Rows are never changed."""

    __tablename__ = "release_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
    model: Mapped[str] = mapped_column(String(250))
    baseline_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    baseline_model: Mapped[str | None] = mapped_column(String(250), nullable=True)
    policy_name: Mapped[str] = mapped_column(String(100))
    policy_snapshot: Mapped[dict] = mapped_column(Json)
    status: Mapped[str] = mapped_column(String(20))
    allowed: Mapped[bool] = mapped_column(Boolean)
    checks: Mapped[list] = mapped_column(Json)
    warnings: Mapped[list] = mapped_column(Json)
    software_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ProposedCase(Base):
    """A test case that a generator suggests. It joins a dataset only after a person approves it."""

    __tablename__ = "proposed_cases"
    __table_args__ = (UniqueConstraint("dataset_name", "case_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(200), index=True)
    case_id: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict] = mapped_column(Json)               # A CaseSpec as a dict.
    source_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_case_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    method: Mapped[str] = mapped_column(String(100))        # transform:NAME, model_variation or production
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)   # Set for cases from production traces.
    expected_verified: Mapped[bool] = mapped_column(Boolean)  # True when the expected result is known to be right.
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | approved | rejected
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class Workspace(Base):
    """A tenant. Production data and API keys belong to one workspace and are never shared."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    settings: Mapped[dict] = mapped_column(Json, default=dict)   # Data policy. See trustbench.dataprotect.
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ApiKey(Base):
    """We store only a hash of the key. The key itself is shown once, when it is made."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(12), index=True)   # The first characters, to find the row quickly.
    key_hash: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(10))                 # read | write | admin
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)     # After this time the key stops working.


class ProductionTrace(Base):
    """One request to an AI system in production. By default it holds measurements only, no text."""

    __tablename__ = "production_traces"
    __table_args__ = (
        UniqueConstraint("workspace_id", "trace_id"),
        Index("ix_production_traces_workspace_started", "workspace_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    trace_id: Mapped[str] = mapped_column(String(64))            # Chosen by the client. Sending it again changes nothing.
    name: Mapped[str] = mapped_column(String(100), index=True)   # The feature or app. For example "support-bot".
    started_at: Mapped[dt.datetime] = mapped_column(index=True)
    duration_ms: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10))              # ok | error
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_enc: Mapped[str | None] = mapped_column(Text, nullable=True)      # Encrypted, redacted text. Only with store_content.
    output_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    redactions: Mapped[dict] = mapped_column(Json, default=dict)            # How many items of each kind were removed.
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[dict] = mapped_column(Json, default=dict)
    feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)    # -1, 0 or 1 from the user.
    sampled: Mapped[bool] = mapped_column(Boolean, default=False)
    eval_state: Mapped[str] = mapped_column(String(20), default="not_sampled")  # not_sampled | pending | done | error
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(index=True)


class TraceSpan(Base):
    """A step inside a trace: a model call, a retrieval, a tool call. Spans form a tree."""

    __tablename__ = "trace_spans"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_pk: Mapped[int] = mapped_column(ForeignKey("production_traces.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    span_id: Mapped[str] = mapped_column(String(64))
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))                # model | retrieval | tool | other
    name: Mapped[str] = mapped_column(String(100))
    offset_ms: Mapped[float] = mapped_column(Float)              # Start, counted from the start of the trace.
    duration_ms: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10))
    attrs: Mapped[dict] = mapped_column(Json, default=dict)


class AuditLog(Base):
    """Who did something sensitive. Rows are only added."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    key_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(50))      # For example trace.reveal
    target: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class JobRow(Base):
    """A task for a worker. The database is the default queue, so no other service is needed."""

    __tablename__ = "jobs"
    # A worker takes the oldest ready job: this index answers that without a sort.
    __table_args__ = (Index("ix_jobs_state_run_after_id", "state", "run_after", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict] = mapped_column(Json, default=dict)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(10), default="queued")   # queued | running | done | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[dt.datetime] = mapped_column(default=utcnow)
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class TraceEvaluation(Base):
    """The result of an online check of a production trace.

    kind says how much to trust it: 'measured' (a rule found it), 'estimated' (a judge model guessed it).
    """

    __tablename__ = "trace_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_pk: Mapped[int] = mapped_column(ForeignKey("production_traces.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    evaluator: Mapped[str] = mapped_column(String(100))
    rubric_version: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(10))
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(Json, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


def _sqlite_settings(dbapi_connection, _record, in_memory: bool) -> None:
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA foreign_keys=ON")           # SQLite does not enforce foreign keys unless asked, so a mistake would pass silently.
    if not in_memory:
        cur.execute("PRAGMA journal_mode=WAL")      # Readers and one writer work at the same time. The API and a worker share a file.
        cur.execute("PRAGMA synchronous=NORMAL")    # Safe with WAL: a crash may lose the last moments, never the database.
        cur.execute("PRAGMA busy_timeout=5000")     # Wait up to 5 s for the writer, instead of failing at once.
    cur.close()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        in_memory = ":memory:" in url or url.rstrip("/").endswith("sqlite:")
        if in_memory:
            engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
        else:
            engine = create_engine(url, connect_args={"timeout": 30})
        event.listen(engine, "connect", lambda c, r: _sqlite_settings(c, r, in_memory))
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=int(os.environ.get("TRUSTBENCH_DB_POOL", "10")), max_overflow=int(os.environ.get("TRUSTBENCH_DB_POOL_OVERFLOW", "20")))


def init_db(engine: Engine) -> sessionmaker:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


class ResponseCache(Base):
    """A model answer kept by its request. The same request later costs no model call.

    The key is a hash of everything that can change the answer (see engine.cache_key). We store the answer
    and its token counts, so the cost of a repeated run is zero and the scores stay the same.
    """

    __tablename__ = "response_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    provider_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    tool_calls: Mapped[list | None] = mapped_column(Json, nullable=True)
    spans: Mapped[list | None] = mapped_column(Json, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
