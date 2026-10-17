"""Split an answer into claims. Each claim is checked on its own later."""

import re

from .judge import Judge, JudgeError, extract_json

MAX_CLAIMS = 20


def sentence_claims(text: str) -> list[str]:
    """A cheap split with no model call. One sentence is one claim."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(p.split()) >= 3][:MAX_CLAIMS]


async def extract_claims(judge: Judge, text: str) -> list[str]:
    """Ask the judge for atomic factual claims. Opinions and questions are skipped."""
    if not text.strip():
        return []
    prompt = (
        "List the factual claims in the text. Write each claim as one short sentence "
        "that can be true or false alone. Replace pronouns with the names they mean. "
        "Skip opinions, questions and instructions.\n\n"
        f"<text>\n{text}\n</text>\n\n"
        'Reply as JSON: {"claims": ["...", "..."]}'
    )
    try:
        data = extract_json(await judge.ask(prompt))
        claims = [str(c).strip() for c in data["claims"] if str(c).strip()]
    except (ValueError, KeyError, TypeError) as e:
        raise JudgeError("The claim list was not readable.") from e
    return claims[:MAX_CLAIMS]
