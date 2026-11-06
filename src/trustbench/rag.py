"""Where in a retrieval pipeline did a case go wrong?

For a case with a context we ask in order:
  1. Was the evidence in the context at all?       no -> retrieval_failure (the model never saw it)
  2. Did the model answer correctly?               no -> evidence_ignored (the model had the evidence)
  3. Is every claim supported by the context?      no -> ungrounded_generation
This blames the first stage that failed. It is a diagnosis from the case design, not proof of the cause.

If the case lists the ids of the retrieved documents (input.retrieved_ids) and the right ones (expected.gold_doc_ids),
the code also computes the retrieval metrics below.
"""

import math
from dataclasses import dataclass, field

from .failures import classify
from .rundata import Record
from .transforms import split_sentences


def recall_at_k(retrieved: list[str], gold: list[str], k: int) -> float | None:
    return len(set(retrieved[:k]) & set(gold)) / len(set(gold)) if gold else None


def precision_at_k(retrieved: list[str], gold: list[str], k: int) -> float | None:
    top = retrieved[:k]
    return len(set(top) & set(gold)) / len(top) if top else None


def reciprocal_rank(retrieved: list[str], gold: list[str]) -> float:
    for i, d in enumerate(retrieved, start=1):
        if d in gold:
            return 1 / i
    return 0.0


def ndcg_at_k(retrieved: list[str], gold: list[str], k: int) -> float | None:
    """Binary relevance. 1 means that the ranking puts all right documents first."""
    if not gold:
        return None
    dcg = sum(1 / math.log2(i + 1) for i, d in enumerate(retrieved[:k], start=1) if d in gold)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal


def evidence_texts(rec: Record) -> list[str]:
    e = rec.expected
    out = [str(f) for f in e.get("required_facts", [])]
    if "answer" in e and not e.get("abstain"):
        out.append(str(e["answer"]))
    return out


@dataclass
class RagDiagnosis:
    stage: str | None                       # The first failing stage. None when the case passed or is not a RAG case.
    evidence_in_context: bool | None
    evidence_position: float | None         # 0 is the start of the context, 1 is the end.
    explanation: str = ""
    retrieval: dict = field(default_factory=dict)


def diagnose(rec: Record) -> RagDiagnosis:
    ctx = rec.input.get("context")
    retrieved, gold = rec.input.get("retrieved_ids"), rec.expected.get("gold_doc_ids")
    if not isinstance(ctx, str) and not retrieved:
        return RagDiagnosis(None, None, None, "The case has no context.")
    retrieval = {}
    if retrieved and gold:
        retrieval = {"recall@5": recall_at_k(retrieved, gold, 5), "precision@5": precision_at_k(retrieved, gold, 5),
                     "mrr": reciprocal_rank(retrieved, gold), "ndcg@5": ndcg_at_k(retrieved, gold, 5)}
    facts = evidence_texts(rec)
    present, position = None, None
    if retrieved and gold:
        present = bool(set(retrieved) & set(gold))
    elif isinstance(ctx, str) and facts:
        sentences = split_sentences(ctx)
        idx = next((i for i, s in enumerate(sentences) if any(f.lower() in s.lower() for f in facts)), None)
        present = idx is not None
        position = idx / max(len(sentences) - 1, 1) if idx is not None else None
    labels = classify(rec)
    if not labels:
        return RagDiagnosis(None, present, position, "The case passed.", retrieval)
    if rec.expected.get("abstain"):
        stage = "failure_to_abstain" if "failure_to_abstain" in labels else None
        return RagDiagnosis(stage, present, position, "The evidence is not in the context. The model should say so." if stage else "", retrieval)
    if present is False:
        return RagDiagnosis("retrieval_failure", False, None, "The evidence never reached the model. Check retrieval and the context first.", retrieval)
    if any(l in labels for l in ("wrong_answer", "judged_incorrect", "unnecessary_refusal")):
        where = f" (at {position:.0%} of the context)" if position is not None else ""
        return RagDiagnosis("evidence_ignored", present, position, f"The evidence was in the context{where}, but the answer is wrong.", retrieval)
    if any(l in labels for l in ("unsupported_claim", "contradicted_claim")):
        return RagDiagnosis("ungrounded_generation", present, position, "The answer contains claims that the context does not support.", retrieval)
    return RagDiagnosis(labels[0], present, position, "", retrieval)
