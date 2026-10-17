"""Read the answer and the stated confidence. Phase 3 uses them for calibration metrics."""

import re

from .evaluators import Ctx, Outcome, normalize, register

ANSWER = re.compile(r"answer\s*[:\-]\s*(.+)", re.I)
CONFIDENCE = re.compile(r"confidence\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(%?)", re.I)


def parse_confidence(text: str) -> float | None:
    """Return a value from 0 to 1. Accept '85%', '85' and '0.85'."""
    m = CONFIDENCE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2) or value > 1:
        value /= 100
    return value if 0 <= value <= 1 else None


@register("calibrated_answer")
def calibrated_answer(ctx: Ctx) -> Outcome:
    """The model must write 'Answer: ...' and 'Confidence: ...%'.

    The case passes when the answer is right and the confidence is readable.
    details has correct and confidence, so we can measure calibration later.
    """
    e = ctx.expected
    refs = e.get("answers") or ([e["answer"]] if "answer" in e else [])
    if not refs:
        return Outcome("calibrated_answer", "1", False, 0.0, "No reference answer in the test case.", {"error": True})
    m = ANSWER.search(ctx.output)
    text = normalize(m.group(1)) if m else ""
    correct = any(re.search(rf"\b{re.escape(normalize(str(r)))}\b", text) for r in refs)
    conf = parse_confidence(ctx.output)
    details = {"correct": correct, "confidence": conf}
    if not m or conf is None:
        missing = "answer line" if not m else "confidence"
        return Outcome("calibrated_answer", "1", False, float(correct), f"The output has no readable {missing}.", details)
    return Outcome("calibrated_answer", "1", correct, float(correct),
                   f"{'Correct' if correct else 'Wrong'} with confidence {conf:.0%}.", details)
