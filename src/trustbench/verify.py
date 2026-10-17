"""Check each claim against the evidence.

Labels:
  supported       the evidence says it
  contradicted    the evidence says the opposite
  insufficient    the evidence does not say it (it may be true in the world)
  not_applicable  we could not judge it (no evidence, or an unreadable judge reply)
"""

import asyncio
import re
from dataclasses import asdict, dataclass

from .judge import Judge, JudgeError, extract_json

LABELS = ("supported", "contradicted", "insufficient", "not_applicable")


@dataclass
class ClaimVerdict:
    claim: str
    label: str
    quote: str = ""          # The part of the evidence the judge used.
    rationale: str = ""
    confidence: float | None = None
    needs_review: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


async def verify_claim(judge: Judge, claim: str, evidence: str | None) -> ClaimVerdict:
    if not evidence or not evidence.strip():
        return ClaimVerdict(claim, "not_applicable", rationale="No evidence was given.")
    prompt = (
        "Check the claim against the evidence only. Do not use outside knowledge.\n"
        "supported = the evidence says it. contradicted = the evidence says the opposite. "
        "insufficient = the evidence does not say it.\n\n"
        f"<evidence>\n{evidence}\n</evidence>\n\n<claim>\n{claim}\n</claim>\n\n"
        'Reply as JSON: {"label": "supported" | "contradicted" | "insufficient", '
        '"quote": "<exact words from the evidence, or empty>", "rationale": "<one sentence>", '
        '"confidence": <0 to 1>}'
    )
    try:
        data = extract_json(await judge.ask(prompt))
        label = str(data["label"]).lower()
        if label not in ("supported", "contradicted", "insufficient"):
            raise ValueError(label)
    except (ValueError, KeyError, TypeError, JudgeError):
        return ClaimVerdict(claim, "not_applicable", rationale="The judge reply was not readable.",
                            needs_review=True)
    quote = str(data.get("quote") or "")
    try:
        conf = max(0.0, min(1.0, float(data["confidence"])))
    except (KeyError, TypeError, ValueError):
        conf = None
    # A quote that is not in the evidence is a sign that the judge made it up.
    bad_quote = bool(quote) and _squash(quote) not in _squash(evidence)
    needs_review = bad_quote or (label != "insufficient" and not quote) or (conf is not None and conf < 0.6)
    return ClaimVerdict(claim, label, quote, str(data.get("rationale", "")), conf, needs_review)


async def verify_claims(judge: Judge, claims: list[str], evidence: str | None) -> list[ClaimVerdict]:
    return list(await asyncio.gather(*(verify_claim(judge, c, evidence) for c in claims)))
