"""Database tables.

Raw model outputs (ModelResponse) and scores (EvaluationResult) live in
different tables. This lets us score old outputs again without a model call.
"""

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class DatasetVersion(Base):
    """An immutable snapshot. The hash comes from the content of the cases."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("name", "content_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
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

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
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


class TestExecution(Base):
    """One test case, run on one model, for one repetition."""

    __test__ = False
    __tablename__ = "test_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
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
    method: Mapped[str] = mapped_column(String(100))        # transform:NAME or model_variation
    expected_verified: Mapped[bool] = mapped_column(Boolean)  # True when the expected result is known to be right.
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | approved | rejected
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite") and (":memory:" in url or url.endswith("//")):
        return create_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    return create_engine(url, pool_pre_ping=True)


def init_db(engine: Engine) -> sessionmaker:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
