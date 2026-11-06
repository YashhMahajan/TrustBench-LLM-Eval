"""Load, check and store datasets. A stored version never changes."""

import csv
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents.schema import validate_agent_fields
from .db import DatasetVersion, TestCase
from .evaluators import EVALUATORS

SUITES_DIR = Path(__file__).parent / "suites"


class CaseSpec(BaseModel):
    id: str
    task: str = "generic"
    input: dict
    expected: dict = {}
    evaluation: dict = {}
    metadata: dict = {}

    @field_validator("input", mode="before")
    @classmethod
    def _wrap_text(cls, v):
        return {"prompt": v} if isinstance(v, str) else v


class DatasetError(ValueError):
    pass


def resolve_path(name_or_path: str) -> Path:
    """A built-in suite name (for example 'smoke') or a file path."""
    builtin = SUITES_DIR / f"{name_or_path}.jsonl"
    if builtin.exists():
        return builtin
    path = Path(name_or_path)
    if not path.exists():
        raise DatasetError(f"Dataset not found: {name_or_path}")
    return path


def load_cases(path: Path) -> list[CaseSpec]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    elif suffix == ".json":
        rows = json.loads(path.read_text())
    elif suffix == ".csv":
        rows = []
        for r in csv.DictReader(path.open(newline="")):
            rows.append(
                {
                    "id": r["id"],
                    "input": r["prompt"],
                    "expected": {"answer": r["answer"]} if r.get("answer") else {},
                    "metadata": {k: v for k, v in r.items() if k not in {"id", "prompt", "answer"}},
                }
            )
    else:
        raise DatasetError(f"Unsupported file type: {suffix}. Use .jsonl, .json or .csv.")
    cases = [CaseSpec(**r) for r in rows]
    validate(cases)
    return cases


def validate(cases: list[CaseSpec]) -> None:
    if not cases:
        raise DatasetError("The dataset has no test cases.")
    seen = set()
    for c in cases:
        if c.id in seen:
            raise DatasetError(f"Duplicate case id: {c.id}")
        seen.add(c.id)
        for spec in c.evaluation.get("evaluators", []):
            if spec.get("type") not in EVALUATORS:
                raise DatasetError(f"Case {c.id}: unknown evaluator '{spec.get('type')}'.")
        try:
            validate_agent_fields(c.input, c.expected, c.evaluation)
        except ValueError as e:
            raise DatasetError(f"Case {c.id}: {e}") from e
        if not c.evaluation.get("evaluators") and not (
            "answer" in c.expected or "answers" in c.expected
        ):
            raise DatasetError(f"Case {c.id}: add an expected answer or an evaluator.")


def content_hash(cases: list[CaseSpec]) -> str:
    canonical = json.dumps(
        [c.model_dump() for c in sorted(cases, key=lambda c: c.id)], sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def import_dataset(session: Session, name: str, cases: list[CaseSpec], workspace_id: str | None = None) -> DatasetVersion:
    """Store the cases. The same content gives the same version. Nothing is overwritten."""
    h = content_hash(cases)
    existing = session.scalar(
        select(DatasetVersion).where(DatasetVersion.name == name, DatasetVersion.content_hash == h,
                                     DatasetVersion.workspace_id == workspace_id if workspace_id else DatasetVersion.workspace_id.is_(None))
    )
    if existing:
        return existing
    version = DatasetVersion(name=name, content_hash=h, case_count=len(cases), workspace_id=workspace_id)
    session.add(version)
    session.flush()
    for c in cases:
        session.add(
            TestCase(
                dataset_version_id=version.id,
                case_id=c.id,
                task=c.task,
                input=c.input,
                expected=c.expected,
                evaluation=c.evaluation,
                meta=c.metadata,
            )
        )
    session.commit()
    return version


def render_prompt(inp: dict) -> str:
    """The text a plain model gets. App targets also receive the full input (see providers/app.py)."""
    if "prompt" in inp:
        return inp["prompt"]
    if "messages" in inp:
        return "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in inp["messages"])
    parts = []
    if inp.get("context"):
        parts.append(f"Context:\n{inp['context']}")
    parts.append(inp.get("question", ""))
    return "\n\n".join(parts)
