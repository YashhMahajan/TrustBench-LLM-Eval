"""Paraphrases written by a model. They are only used when they pass two checks.

Check 1 (no model): numbers, quoted words and words that decide the answer must stay.
Check 2 (a second model call): a judge must confirm that both texts ask for the same thing.
A paraphrase that fails a check is dropped, because it might change what the right answer is.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from .datasets import CaseSpec
from .judge import Judge, JudgeError, extract_json
from .transforms import protected_words, quoted_words


def text_key(case: CaseSpec) -> str | None:
    """The field that holds the question: 'prompt' or 'question'."""
    return next((k for k in ("prompt", "question") if isinstance(case.input.get(k), str)), None)


def _numbers(text: str) -> Counter:
    return Counter(re.findall(r"\d+(?:[.,]\d+)*", text))


def check_paraphrase(case: CaseSpec, original: str, new: str) -> str | None:
    """Return the reason the paraphrase is not acceptable, or None."""
    if " ".join(new.lower().split()) == " ".join(original.lower().split()):
        return "the text is the same"
    if _numbers(original) != _numbers(new):
        return "the numbers changed"
    low = new.lower()
    lost = sorted(w for w in quoted_words(original) | (protected_words(case) & set(re.findall(r"[a-z]{3,}", original.lower())))
                  if w not in low)
    if lost:
        return f"the word '{lost[0]}' was lost"
    if not 0.5 <= len(new) / max(len(original), 1) <= 2.5:
        return "the length changed too much"
    return None


@dataclass
class ParaphraseReport:
    applied: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


async def paraphrase_case(judge: Judge, case: CaseSpec) -> tuple[CaseSpec | None, str]:
    key = text_key(case)
    if key is None:
        return None, "no question text"
    original = case.input[key]
    prompt = (
        "Rewrite the text with different words and a different sentence structure. "
        "Keep every fact, number, name, quoted word and instruction exactly as they are, "
        "including instructions about the format of the answer. Do not answer the text.\n\n"
        f"<text>\n{original}\n</text>\n\n"
        'Reply as JSON: {"text": "<the rewritten text>"}'
    )
    try:
        new = str(extract_json(await judge.ask(prompt))["text"]).strip()
    except (ValueError, KeyError, TypeError, JudgeError):
        return None, "unreadable reply"
    reason = check_paraphrase(case, original, new)
    if reason:
        return None, reason
    check = (
        "Do these two texts ask for exactly the same thing, so that the same answer is right for both? "
        "Pay attention to numbers, names, constraints and the answer format.\n\n"
        f"<first>\n{original}\n</first>\n\n<second>\n{new}\n</second>\n\n"
        'Reply as JSON: {"same": true | false}'
    )
    try:
        same = extract_json(await judge.ask(check)).get("same") is True
    except (ValueError, AttributeError, JudgeError):
        return None, "unreadable check"
    if not same:
        return None, "the judge says the meaning changed"
    meta = {**case.metadata, "source_case": case.id, "transform": "paraphrase"}
    return case.model_copy(update={"id": f"{case.id}~paraphrase", "input": {**case.input, key: new}, "metadata": meta}), ""


async def build_paraphrases(judge: Judge, cases: list[CaseSpec]) -> tuple[list[CaseSpec], ParaphraseReport]:
    out, rep = [], ParaphraseReport()
    for case in cases:
        if case.metadata.get("source_case"):
            continue
        variant, reason = await paraphrase_case(judge, case)
        if variant:
            out.append(variant)
            rep.applied += 1
        else:
            rep.reject(reason)
    return out, rep
