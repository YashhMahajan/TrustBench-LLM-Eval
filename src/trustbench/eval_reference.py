"""Reference-based scoring (token F1) and abstention checks."""

import re
from collections import Counter

from .evaluators import Ctx, Outcome, register


def tokens(text: str) -> list[str]:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return [t for t in text.split() if t not in {"a", "an", "the"}]


def f1(pred: str, ref: str) -> float:
    p, r = tokens(pred), tokens(ref)
    common = sum((Counter(p) & Counter(r)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(p), common / len(r)
    return 2 * precision * recall / (precision + recall)


@register("token_f1")
def token_f1(ctx: Ctx) -> Outcome:
    """Word overlap with the best reference answer. spec: threshold (default 0.7)."""
    e = ctx.expected
    refs = e.get("answers") or ([e["answer"]] if "answer" in e else [])
    if not refs:
        return Outcome("token_f1", "1", False, 0.0, "No reference answer in the test case.", {"error": True})
    score = max(f1(ctx.output, str(r)) for r in refs)
    threshold = ctx.spec.get("threshold", 0.7)
    return Outcome("token_f1", "1", score >= threshold, score, f"F1 {score:.2f}, threshold {threshold}.")


ABSTAIN_PATTERNS = [
    r"\bi (do not|don'?t) know\b",
    r"\b(cannot|can'?t|unable to|not able to) (answer|determine|find|say|tell|help|assist|provide|comply)\b",
    r"\bi (won'?t|will not)\b",
    r"\bnot (provided|mentioned|stated|specified|available|included)\b",
    r"\b(no|insufficient) (information|evidence|data)\b",
    r"\bdoes(n'?t| not) (say|mention|specify|state|contain)\b",
]


def abstained(text: str, extra: list[str] | None = None) -> bool:
    patterns = ABSTAIN_PATTERNS + [re.escape(p) for p in (extra or [])]
    return any(re.search(p, text, re.I) for p in patterns)


@register("abstain")
def abstain(ctx: Ctx) -> Outcome:
    """Does the model abstain (or refuse) exactly when it should?

    The test case sets expected.abstain to true or false. This is a phrase
    check. It is a fast first filter, not a full understanding of the text.
    """
    should = ctx.expected.get("abstain")
    if should is None:
        return Outcome("abstain", "1", False, 0.0, "expected.abstain is missing.", {"error": True})
    did = abstained(ctx.output, ctx.spec.get("phrases"))
    if did == should:
        return Outcome("abstain", "1", True, 1.0, "Abstained." if did else "Answered.", {"abstained": did})
    failure = "unnecessary_refusal" if did else "failure_to_abstain"
    msg = "The model refused, but it should answer." if did else "The model answered, but it should abstain."
    return Outcome("abstain", "1", False, 0.0, msg, {"abstained": did, "failure": failure})
