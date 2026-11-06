"""Blind pairwise comparison. The judge judges each pair twice, in both orders.

If the judge picks a different answer when the order changes, the verdict is a tie. We mark it as inconsistent. This shows position bias.
"""

from dataclasses import dataclass

from .judge import Judge, JudgeError, extract_json


@dataclass
class PairVerdict:
    winner: str          # "A", "B" or "tie"
    consistent: bool
    rationale: str = ""


def _prompt(question: str, first: str, second: str, reference: str | None) -> str:
    parts = ["Decide which answer is better. Judge correctness and helpfulness. "
             "Ignore the order of the answers and ignore their length."]
    if question:
        parts.append(f"<question>\n{question}\n</question>")
    if reference:
        parts.append(f"<reference_answer>\n{reference}\n</reference_answer>")
    parts.append(f"<first_answer>\n{first}\n</first_answer>")
    parts.append(f"<second_answer>\n{second}\n</second_answer>")
    parts.append('Reply as JSON: {"rationale": "<one sentence>", "winner": "first" | "second" | "tie"}')
    return "\n\n".join(parts)


async def _ask(judge: Judge, question, first, second, reference) -> tuple[str, str]:
    data = extract_json(await judge.ask(_prompt(question, first, second, reference)))
    winner = str(data.get("winner", "")).lower()
    if winner not in {"first", "second", "tie"}:
        raise JudgeError("The judge reply was not readable.")
    return winner, str(data.get("rationale", ""))


async def compare(judge: Judge, question: str, answer_a: str, answer_b: str,
                  reference: str | None = None) -> PairVerdict:
    try:
        w1, why = await _ask(judge, question, answer_a, answer_b, reference)
        w2, _ = await _ask(judge, question, answer_b, answer_a, reference)
    except ValueError as e:  # extract_json found no JSON.
        raise JudgeError(str(e)) from e
    to_ab = {"first": "A", "second": "B", "tie": "tie"}
    swap = {"first": "B", "second": "A", "tie": "tie"}
    v1, v2 = to_ab[w1], swap[w2]
    if v1 == v2:
        return PairVerdict(v1, True, why)
    return PairVerdict("tie", False, "The judge changed its pick when the order changed.")


def win_rate_a(verdicts: list[PairVerdict]) -> float | None:
    """Share of wins for A. A tie counts as half a win."""
    if not verdicts:
        return None
    return sum({"A": 1.0, "tie": 0.5, "B": 0.0}[v.winner] for v in verdicts) / len(verdicts)


def inconsistency_rate(verdicts: list[PairVerdict]) -> float | None:
    return sum(not v.consistent for v in verdicts) / len(verdicts) if verdicts else None
