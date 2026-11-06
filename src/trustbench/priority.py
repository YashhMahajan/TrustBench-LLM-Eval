"""Which failures should we fix first?

priority = severity weight x frequency x exposure x detectability

All four factors are shown. Nothing is hidden in one opaque number.
  severity       1 to 5. From the case (metadata.severity, risk or critical) or from the failure type, whichever is higher.
  weight         grows fast with severity, so a rare severe failure can outrank a frequent small one.
  frequency      share of all cases that fail this way (in percent).
  exposure       metadata.exposure of the cases (default 1). Use it for how often real users meet this case.
  detectability  1.0 when a deterministic check finds it. 1.5 when only a judge model or a person can find it.
"""

from dataclasses import dataclass

from .failures import Failure

WEIGHT = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
LABEL_SEVERITY = {
    "prompt_injection_failure": 5, "contradicted_claim": 4, "failure_to_abstain": 3, "unsupported_claim": 3,
    "wrong_answer": 3, "judged_incorrect": 3, "unnecessary_refusal": 2, "format_violation": 2,
    "instruction_violation": 2, "task_not_done": 2, "wrong_tool": 3, "bad_args": 3, "looped": 3,
    "gave_up": 3, "task_not_completed": 3, "irrelevant_answer": 2, "poor_retrieval": 2, "inconsistent_behavior": 2, "call_failure": 2, "timeout": 2,
}
RISK = {"critical": 5, "high": 4, "medium": 3, "low": 1}
JUDGE_BASED = {"llm_judge", "grounded_claims", "rag_faithfulness", "rag_answer_relevance", "rag_context_precision", "rag_context_recall"}


def case_severity(f: Failure) -> int:
    meta = f.record.metadata if f.record else {}
    s = max([LABEL_SEVERITY.get(l, 2) for l in f.labels] or [2])
    if meta.get("critical"):
        s = 5
    s = max(s, RISK.get(str(meta.get("risk")), 0))
    if isinstance(meta.get("severity"), int):
        s = max(s, min(5, max(1, meta["severity"])))
    return s


@dataclass
class PriorityItem:
    name: str
    cases: int
    severity: int
    weight: int
    frequency_pct: float
    exposure: float
    detectability: float
    score: float


def prioritize(groups: dict[str, list[Failure]], total_cases: int) -> list[PriorityItem]:
    items = []
    for name, members in groups.items():
        if not members:
            continue
        sev = max(case_severity(f) for f in members)
        exposure = sum((f.record.metadata.get("exposure", 1.0) if f.record else 1.0) for f in members) / len(members)
        judged = any(r["evaluator"] in JUDGE_BASED for f in members if f.record for r in f.record.results if not r["passed"])
        freq = 100 * len(members) / total_cases if total_cases else 0.0
        det = 1.5 if judged else 1.0
        items.append(PriorityItem(name, len(members), sev, WEIGHT[sev], freq, exposure, det, WEIGHT[sev] * freq * exposure * det))
    return sorted(items, key=lambda i: (-i.score, i.name))
