"""Compare a judge with human labels. This tells us how far we can trust the judge."""

import statistics
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .agreement import cohens_kappa, krippendorff_alpha, percent_agreement, weighted_kappa
from .db import EvaluationResult, HumanAnnotation, ModelResponse, TestExecution
from .stats import wilson_interval

MIN_LABELS = 30  # With fewer labels, the numbers are too uncertain to trust.


@dataclass
class JudgeValidation:
    n: int = 0
    exact_agreement: float | None = None
    exact_agreement_ci: tuple[float, float] | None = None
    kappa: float | None = None
    weighted_kappa: float | None = None
    pass_agreement: float | None = None
    false_pass_rate: float | None = None    # The judge passes an answer that people fail.
    false_fail_rate: float | None = None    # The judge fails an answer that people pass.
    human_alpha: float | None = None        # Agreement between the people. Needs 2 or more annotators.
    judge_review_rate: float | None = None  # Share of grades the judge itself flagged.
    warnings: list[str] = field(default_factory=list)


def _human_label(labels: list[float]) -> int:
    return int(statistics.median(labels) + 0.5)  # Median of the people. A half rounds up.


def validate_judge(factory: sessionmaker, run_id: str, dimension: str = "correctness",
                   evaluator: str = "llm_judge") -> JudgeValidation:
    with factory() as s:
        batch = s.scalar(
            select(func.max(EvaluationResult.batch))
            .join(ModelResponse, ModelResponse.id == EvaluationResult.response_id)
            .join(TestExecution, TestExecution.id == ModelResponse.execution_id)
            .where(TestExecution.run_id == run_id)) or 0
        rows = s.execute(
            select(HumanAnnotation, EvaluationResult)
            .join(ModelResponse, ModelResponse.id == HumanAnnotation.response_id)
            .join(TestExecution, TestExecution.id == ModelResponse.execution_id)
            .join(EvaluationResult, EvaluationResult.response_id == HumanAnnotation.response_id)
            .where(TestExecution.run_id == run_id, HumanAnnotation.dimension == dimension,
                   EvaluationResult.evaluator == evaluator, EvaluationResult.batch == batch)).all()

    by_response: dict[int, dict] = {}
    for ann, res in rows:
        d = by_response.setdefault(ann.response_id, {"labels": {}, "result": res})
        d["labels"][ann.annotator] = ann.label
    v = JudgeValidation()
    usable = [d for d in by_response.values() if "raw_score" in d["result"].details]
    v.n = len(usable)
    if not usable:
        v.warnings.append("No response has both a human label and a judge grade.")
        return v

    human = [_human_label(list(d["labels"].values())) for d in usable]
    judge = [int(round(d["result"].details["raw_score"])) for d in usable]
    levels = list(range(usable[0]["result"].details["max_score"] + 1))
    agree = sum(h == j for h, j in zip(human, judge))
    v.exact_agreement = percent_agreement(human, judge)
    v.exact_agreement_ci = wilson_interval(agree, v.n)
    v.kappa = cohens_kappa(human, judge)
    v.weighted_kappa = weighted_kappa(human, judge, levels)

    human_pass = [h >= d["result"].details["pass_at"] for h, d in zip(human, usable)]
    judge_pass = [d["result"].passed for d in usable]
    v.pass_agreement = percent_agreement(human_pass, judge_pass)
    human_fail = [i for i, p in enumerate(human_pass) if not p]
    human_ok = [i for i, p in enumerate(human_pass) if p]
    if human_fail:
        v.false_pass_rate = sum(judge_pass[i] for i in human_fail) / len(human_fail)
    if human_ok:
        v.false_fail_rate = sum(not judge_pass[i] for i in human_ok) / len(human_ok)
    v.judge_review_rate = sum(bool(d["result"].details.get("human_review")) for d in usable) / v.n

    annotators = sorted({a for d in usable for a in d["labels"]})
    if len(annotators) >= 2:
        v.human_alpha = krippendorff_alpha(
            [[d["labels"].get(a) for a in annotators] for d in usable], level="interval")
    if v.n < MIN_LABELS:
        v.warnings.append(f"Only {v.n} labelled answers. Use at least {MIN_LABELS} before you trust these numbers.")
    if v.kappa is None:
        v.warnings.append("Kappa is undefined because all labels are the same.")
    if len(annotators) < 2:
        v.warnings.append("One annotator only. Human agreement is unknown.")
    return v
