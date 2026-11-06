"""Experiments: the history of an EDD session.

An experiment is a named variant (v0, v1, ...) of the system under test. It points at the run that measured it,
at the variant it changed (its parent) and at the hypothesis behind the change. `compare_with_parent` says,
with the same statistics as `trustbench compare`, whether the evidence supports the hypothesis.
"""

import difflib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ..baselines import resolve
from ..comparison import Comparison, compare_runs
from ..db import EvaluationRun, Experiment
from ..rundata import load_run
from ..scorecard import Score, scorecard
from ..slices import SliceDelta, regression_scan
from .costmodel import CostModel, CostReport, cost_report

IMPROVED = ("meaningful_improvement", "improvement_below_threshold")
WORSE = ("meaningful_regression", "regression_below_threshold")


class ExperimentError(ValueError):
    pass


def _same_workspace(workspace_id):
    return Experiment.workspace_id == workspace_id if workspace_id else Experiment.workspace_id.is_(None)


def get(s, name: str, workspace_id: str | None = None) -> Experiment:
    row = s.scalars(select(Experiment).where(Experiment.name == name, _same_workspace(workspace_id))).first()
    if row is None:
        raise ExperimentError(f"No experiment named '{name}'. List them with 'trustbench experiment list'.")
    return row


def record(factory: sessionmaker, name: str, run_ref: str, parent: str | None = None, hypothesis: str = "",
           model: str | None = None, workspace_id: str | None = None) -> Experiment:
    """Name a run as a variant. `run_ref` is a run id or a baseline name. Names are history. Do not reuse them."""
    if not name.strip():
        raise ExperimentError("An experiment needs a name.")
    run_id, run_model = resolve(factory, run_ref)
    model = load_run(factory, run_id).pick_model(model or run_model)       # A run with several models needs a choice now, not later.
    with factory.begin() as s:
        if s.scalars(select(Experiment).where(Experiment.name == name, _same_workspace(workspace_id))).first():
            raise ExperimentError(f"An experiment named '{name}' exists. Use a new name: experiments are a history.")
        parent_id = get(s, parent, workspace_id).id if parent else None
        row = Experiment(workspace_id=workspace_id, name=name, run_id=run_id, model=model,
                         parent_id=parent_id, hypothesis=hypothesis)
        s.add(row)
        s.flush()
        s.expunge(row)
        return row


@dataclass
class TimelineRow:
    name: str
    parent: str | None
    run_id: str
    model: str | None
    hypothesis: str
    score: Score
    created_at: object


def timeline(factory: sessionmaker, workspace_id: str | None = None) -> list[TimelineRow]:
    """All experiments, oldest first, each with the pass rate of its run."""
    with factory() as s:
        rows = list(s.scalars(select(Experiment).where(_same_workspace(workspace_id)).order_by(Experiment.id)))
        names = {r.id: r.name for r in rows}
    out = []
    for r in rows:
        data = load_run(factory, r.run_id)
        out.append(TimelineRow(r.name, names.get(r.parent_id), r.run_id, r.model, r.hypothesis,
                               scorecard(data.for_model(r.model)).overall, r.created_at))
    return out


def prompt_diff(factory: sessionmaker, parent_run: str, child_run: str) -> str:
    """A unified diff of the system prompts of two runs. Empty when they are the same."""
    def prompt(run_id: str) -> str:
        with factory() as s:
            return ((s.get(EvaluationRun, run_id).config_snapshot or {}).get("options") or {}).get("system_prompt") or ""
    a, b = prompt(parent_run), prompt(child_run)
    if a == b:
        return ""
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"{parent_run} (parent)", f"{child_run}", n=2))


@dataclass
class ExperimentReport:
    name: str
    parent: str
    hypothesis: str
    verdict: str                                      # supported | refuted | mixed | no_effect | cannot_tell
    comparison: Comparison
    regressed_slices: list[SliceDelta]
    prompt_diff: str
    cost: tuple[CostReport, CostReport] | None        # (parent, child) when the policy has a cost model
    warnings: list[str] = field(default_factory=list)


def hypothesis_verdict(comparison: Comparison, regressed: list[SliceDelta]) -> str:
    """Read the overall change and the slices together. An overall gain that hides a slice that got worse is `mixed`."""
    v = comparison.overall.verdict
    if v in IMPROVED:
        return "mixed" if regressed else "supported"
    if v in WORSE:
        return "refuted"
    return "no_effect" if v == "equivalent" else "cannot_tell"


def compare_runs_of(factory: sessionmaker, name: str, parent_name: str, hypothesis: str, child_run: str, parent_run: str,
                    c_model: str | None, p_model: str | None, min_effect: float = 0.01,
                    cost_model: CostModel | None = None) -> ExperimentReport:
    """Compare the run of a variant with the run of its parent. The command and the API both use this."""
    base, cand = load_run(factory, parent_run), load_run(factory, child_run)
    cmp = compare_runs(base, cand, p_model, c_model, min_effect)
    regressed = [x for x in regression_scan(base.for_model(p_model), cand.for_model(c_model)) if x.regression]
    cost = None
    if cost_model is not None:
        cost = (cost_report(cost_model, base.for_model(p_model)), cost_report(cost_model, cand.for_model(c_model)))
    return ExperimentReport(name, parent_name, hypothesis, hypothesis_verdict(cmp, regressed), cmp, regressed,
                            prompt_diff(factory, parent_run, child_run), cost, list(cmp.warnings))


def compare_with_parent(factory: sessionmaker, name: str, min_effect: float = 0.01, cost_model: CostModel | None = None,
                        workspace_id: str | None = None) -> ExperimentReport:
    with factory() as s:
        child = get(s, name, workspace_id)
        if child.parent_id is None:
            raise ExperimentError(f"'{name}' has no parent. Record it with --parent to compare it.")
        parent = s.get(Experiment, child.parent_id)
        args = (name, parent.name, child.hypothesis, child.run_id, parent.run_id, child.model, parent.model)
    return compare_runs_of(factory, *args, min_effect=min_effect, cost_model=cost_model)
