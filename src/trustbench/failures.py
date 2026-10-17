"""Name what went wrong. A score says that a case failed. A label says how.

The labels come from rules on the evaluator results, so each label can be explained.
A case can have several labels. A label is a description of the symptom, not a proof of the cause.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from .rundata import Record, case_outcomes

TAXONOMY = {
    "wrong_answer": "The answer does not match the reference",
    "judged_incorrect": "A judge model graded the answer as not correct",
    "unsupported_claim": "The answer contains a claim that the evidence does not support",
    "contradicted_claim": "The answer contains a claim that the evidence contradicts",
    "instruction_violation": "The answer breaks an explicit rule, such as a word limit",
    "format_violation": "The output is not in the required format",
    "unnecessary_refusal": "The model refused a request that it should answer",
    "failure_to_abstain": "The model answered when it should say it does not know",
    "prompt_injection_failure": "The model followed an instruction hidden in untrusted text",
    "task_not_done": "The model resisted an attack but did not do the real task",
    "inconsistent_behavior": "The model passed the original case but failed a copy that means the same",
    "call_failure": "The model call failed, so there is no answer",
    "timeout": "The model call took too long",
    "evaluation_error": "An evaluator broke. This is not a model failure.",
}


@dataclass
class Failure:
    case_id: str
    model: str
    task: str
    labels: list[str]
    reasons: list[str]            # The evaluator explanations.
    output: str | None
    needs_review: bool = False
    record: Record | None = field(default=None, repr=False)


def classify(rec: Record) -> list[str]:
    """Labels for one execution. An empty list means that nothing went wrong."""
    labels: list[str] = []
    if rec.status == "failed":
        labels.append("call_failure")
        if rec.timed_out:
            labels.append("timeout")
        return labels
    if rec.status != "done":
        return labels
    if rec.evaluator_error:
        return ["evaluation_error"]
    for res in rec.results:
        if res["passed"]:
            continue
        ev, d = res["evaluator"], res["details"]
        if ev in ("exact_match", "contains", "numeric", "token_f1"):
            labels.append("wrong_answer")
        elif ev == "calibrated_answer":
            labels.append("wrong_answer" if d.get("confidence") is not None else "format_violation")
        elif ev == "llm_judge":
            labels.append("judged_incorrect")
        elif ev == "rule":
            labels.append("instruction_violation")
        elif ev == "json_schema":
            labels.append("format_violation")
        elif ev == "abstain":
            labels.append(d.get("failure") or "wrong_answer")
        elif ev == "injection_resisted":
            labels.append("prompt_injection_failure" if d.get("obeyed") else "task_not_done")
        elif ev == "grounded_claims":
            kinds = {c["label"] for c in d.get("claims", [])}
            if "contradicted" in kinds:
                labels.append("contradicted_claim")
            if "insufficient" in kinds or not kinds:
                labels.append("unsupported_claim")
        else:
            labels.append("wrong_answer")
    return list(dict.fromkeys(labels))


def collect_failures(records: list[Record]) -> list[Failure]:
    """One entry for each failing case (the first failing repetition). Also marks inconsistent copies."""
    outcomes = case_outcomes(records)
    first_bad: dict[str, Record] = {}
    for r in records:
        if r.case_id not in first_bad and classify(r):
            first_bad[r.case_id] = r
    out = []
    for cid, rec in first_bad.items():
        labels = classify(rec)
        src = rec.metadata.get("source_case")
        if src and outcomes.get(src) == 1.0 and "evaluation_error" not in labels and "call_failure" not in labels:
            labels = ["inconsistent_behavior", *labels]
        reasons = [r["rationale"] for r in rec.results if not r["passed"] and r.get("rationale")]
        if rec.error:
            reasons.append(rec.error)
        review = any(r["details"].get("human_review") for r in rec.results)
        out.append(Failure(cid, rec.model, rec.task, labels, reasons, rec.output, review, rec))
    return out


@dataclass
class LabelCount:
    label: str
    cases: int
    share: float                  # Of all cases with a result.
    case_ids: list[str]


def label_counts(failures: list[Failure], total_cases: int) -> list[LabelCount]:
    by: dict[str, list[str]] = defaultdict(list)
    for f in failures:
        for lab in f.labels:
            by[lab].append(f.case_id)
    rows = [LabelCount(lab, len(ids), len(ids) / total_cases if total_cases else 0.0, sorted(ids)) for lab, ids in by.items()]
    return sorted(rows, key=lambda r: -r.cases)
