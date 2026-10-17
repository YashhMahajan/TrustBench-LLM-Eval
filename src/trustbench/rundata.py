"""Load a run into plain Python objects. All analysis works on these, not on the database."""

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .datasets import render_prompt
from .db import (
    DatasetVersion, EvaluationResult, EvaluationRun, ModelConfiguration, ModelResponse, TestCase, TestExecution,
)


@dataclass
class Record:
    """One execution: a case, a model and a repetition."""
    case_id: str = "c"
    model: str = "m:m"
    repetition: int = 0
    task: str = "generic"
    metadata: dict = field(default_factory=dict)
    input_words: int = 0
    status: str = "done"                # done | failed | skipped | pending
    passed: bool | None = None          # None: no score (failed call, skipped, or evaluator problem)
    evaluator_error: bool = False       # The evaluator broke. This says nothing about the model.
    attempts: int = 1
    timed_out: bool = False
    latency_ms: float | None = None
    cost_usd: float | None = None
    results: list = field(default_factory=list)
    input: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)
    output: str | None = None           # The last answer, or None if every call failed.
    error: str | None = None            # The last error, when the final call failed.

    @property
    def critical(self) -> bool:
        return bool(self.metadata.get("critical"))


@dataclass
class RunData:
    run_id: str
    status: str
    dataset: str
    dataset_hash: str
    judge_cost_usd: float
    records: list[Record]

    @cached_property
    def models(self) -> list[str]:
        return sorted({r.model for r in self.records})

    def for_model(self, model: str | None = None) -> list[Record]:
        chosen = self.pick_model(model)        # Once. Doing this for every record made it O(n squared).
        return [r for r in self.records if r.model == chosen]

    def pick_model(self, model: str | None = None) -> str:
        if model is None:
            if len(self.models) != 1:
                raise ValueError(f"Run {self.run_id} has several models: {', '.join(self.models)}. Choose one with --model.")
            return self.models[0]
        if model not in self.models:
            raise ValueError(f"Run {self.run_id} has no model '{model}'. It has: {', '.join(self.models)}.")
        return model


def load_run(factory: sessionmaker, run_id: str, batch: int | None = None) -> RunData:
    with factory() as s:
        run = s.get(EvaluationRun, run_id)
        if run is None:
            raise ValueError(f"No run '{run_id}'.")
        version = s.get(DatasetVersion, run.dataset_version_id)
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
            .where(TestExecution.run_id == run_id)
            .order_by(TestExecution.id)).all()
        responses: dict[int, list[ModelResponse]] = defaultdict(list)
        for r in s.scalars(select(ModelResponse).join(TestExecution, TestExecution.id == ModelResponse.execution_id)
                           .where(TestExecution.run_id == run_id).order_by(ModelResponse.attempt)):
            responses[r.execution_id].append(r)
        results: dict[int, list[EvaluationResult]] = defaultdict(list)
        for r in s.scalars(select(EvaluationResult)
                           .join(ModelResponse, ModelResponse.id == EvaluationResult.response_id)
                           .join(TestExecution, TestExecution.id == ModelResponse.execution_id)
                           .where(TestExecution.run_id == run_id, EvaluationResult.batch == batch)):
            results[r.response_id].append(r)

        records = []
        for ex, case, mc in execs:
            attempts = responses.get(ex.id, [])
            last = attempts[-1] if attempts else None
            res = results.get(last.id, []) if last and last.ok else []
            rec = Record(
                case_id=case.case_id, model=f"{mc.provider}:{mc.model}", repetition=ex.repetition, task=case.task,
                metadata=case.meta, input_words=len(render_prompt(case.input).split()), status=ex.status,
                attempts=len(attempts), timed_out=any(a.error == "Timeout" for a in attempts),
                latency_ms=last.latency_ms if last and last.ok else None,
                cost_usd=last.cost_usd if last and last.ok else None,
                input=case.input, expected=case.expected,
                output=last.output if last and last.ok else None, error=last.error if last and not last.ok else None,
                results=[{"evaluator": r.evaluator, "passed": r.passed, "score": r.score, "details": r.details,
                          "rationale": r.rationale, "rubric_version": r.rubric_version} for r in res])
            if res:
                rec.evaluator_error = any(r.details.get("error") for r in res)
                rec.passed = None if rec.evaluator_error else all(r.passed for r in res)
            records.append(rec)
        return RunData(run_id, run.status, version.name, version.content_hash, run.judge_cost_usd, records)


def case_outcomes(records: list[Record]) -> dict[str, float | None]:
    """One number for each case: the share of repetitions that passed.

    A failed model call counts as a fail, because the model gave no answer.
    A skipped case or an evaluator problem is left out (None), because it says nothing about the model.
    """
    by_case: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_case.setdefault(r.case_id, [])
        if r.status == "failed":
            by_case[r.case_id].append(0.0)
        elif r.status == "done" and r.passed is not None:
            by_case[r.case_id].append(float(r.passed))
    return {cid: (sum(v) / len(v) if v else None) for cid, v in by_case.items()}
