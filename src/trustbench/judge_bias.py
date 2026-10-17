"""Probes that test the judge itself. We know the right result, so a miss shows a bias.

position    The right answer must win in both orders.
verbosity   Extra words with no new information must not raise the score.
wording     A small change in the question text must not change the score.
"""

import statistics
from dataclasses import dataclass

from .datasets import CaseSpec, render_prompt
from .eval_judge import reference_of
from .judge import Judge, JudgeError, Rubric, grade
from .pairwise import compare

FILLER = (" To say this in other words, and at greater length, the answer above is the one that matters here. "
          "It is worth stating it again with care, so that the point is fully clear to every reader.")


@dataclass
class BiasReport:
    cases: int = 0
    errors: int = 0                          # Probes where the judge reply was not readable.
    position_pairs: int = 0
    position_inconsistency: float | None = None  # Share of pairs where the pick changed with the order.
    correct_pick_rate: float | None = None       # Share of pairs where the reference answer won.
    verbosity_pairs: int = 0
    verbosity_mean_delta: float | None = None    # Score of the padded answer minus the plain one.
    wording_pairs: int = 0
    wording_mean_abs_change: float | None = None


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


async def run_bias_probes(judge: Judge, cases: list[CaseSpec], rubric: Rubric, max_cases: int = 20) -> BiasReport:
    usable = [c for c in cases if reference_of(c.expected)][:max_cases]
    report = BiasReport(cases=len(usable))
    inconsistent, correct, deltas, changes = [], [], [], []
    for i, case in enumerate(usable):
        question, ref = render_prompt(case.input), reference_of(case.expected)
        wrong = reference_of(usable[(i + 1) % len(usable)].expected)
        try:
            if wrong and wrong != ref:
                v = await compare(judge, question, ref, wrong, reference=ref)
                inconsistent.append(not v.consistent)
                correct.append(v.winner == "A")
            plain = await grade(judge, rubric, question, ref, ref)
            padded = await grade(judge, rubric, question, ref + FILLER, ref)
            deltas.append(padded.score - plain.score)
            reworded = await grade(judge, rubric, f"Please answer the following.\n\n\n{question.upper()}  ", ref, ref)
            changes.append(abs(reworded.score - plain.score))
        except JudgeError:
            report.errors += 1
    report.position_pairs, report.verbosity_pairs, report.wording_pairs = len(inconsistent), len(deltas), len(changes)
    report.position_inconsistency = _mean([float(x) for x in inconsistent])
    report.correct_pick_rate = _mean([float(x) for x in correct])
    report.verbosity_mean_delta = _mean(deltas)
    report.wording_mean_abs_change = _mean(changes)
    return report
