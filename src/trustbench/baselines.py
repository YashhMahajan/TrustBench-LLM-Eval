"""Named baselines and the record of gate decisions."""

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import __version__
from .db import Baseline, EvaluationRun, ReleaseDecision
from .gate import Decision
from .policy import ReleasePolicy


def set_baseline(factory: sessionmaker, name: str, run_id: str, model: str | None = None, workspace_id: str | None = None) -> None:
    with factory.begin() as s:
        if s.get(EvaluationRun, run_id) is None:
            raise ValueError(f"No run '{run_id}'.")
        row = s.get(Baseline, name)
        if row:
            row.run_id, row.model, row.workspace_id = run_id, model, workspace_id or row.workspace_id
        else:
            s.add(Baseline(name=name, run_id=run_id, model=model, workspace_id=workspace_id))


def list_baselines(factory: sessionmaker) -> list[Baseline]:
    with factory() as s:
        return list(s.scalars(select(Baseline).order_by(Baseline.name)))


def resolve(factory: sessionmaker, ref: str) -> tuple[str, str | None]:
    """A baseline name gives its run and model. Anything else must be a run id."""
    with factory() as s:
        b = s.get(Baseline, ref)
        if b:
            return b.run_id, b.model
        if s.get(EvaluationRun, ref) is None:
            raise ValueError(f"'{ref}' is not a baseline name or a run id.")
    return ref, None


def save_decision(factory: sessionmaker, run_id: str, model: str, baseline: tuple[str, str | None] | None,
                  policy: ReleasePolicy, decision: Decision) -> int:
    with factory.begin() as s:
        row = ReleaseDecision(
            run_id=run_id, model=model,
            baseline_run_id=baseline[0] if baseline else None, baseline_model=baseline[1] if baseline else None,
            policy_name=policy.name, policy_snapshot=policy.model_dump(mode="json"),
            status=decision.status, allowed=decision.allowed,
            checks=[c.to_dict() for c in decision.checks], warnings=decision.warnings,
            software_version=__version__)
        s.add(row)
        s.flush()
        return row.id


def list_decisions(factory: sessionmaker, run_id: str | None = None) -> list[ReleaseDecision]:
    with factory() as s:
        q = select(ReleaseDecision).order_by(ReleaseDecision.id.desc())
        if run_id:
            q = q.where(ReleaseDecision.run_id == run_id)
        return list(s.scalars(q))
