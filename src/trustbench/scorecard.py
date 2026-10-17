"""Pass rates with their uncertainty. A rate without its interval and size is not shown."""

from dataclasses import dataclass

from .rundata import Record, case_outcomes
from .stats import bootstrap_ci, mean, wilson_interval


@dataclass
class Score:
    n: int                            # Number of cases.
    rate: float | None
    ci: tuple[float, float] | None    # 95% interval.
    method: str = ""                  # How the interval was made.

    @property
    def text(self) -> str:
        if self.rate is None:
            return "-"
        return f"{self.rate:.1%} ({self.ci[0]:.1%} to {self.ci[1]:.1%}), n={self.n}"


def rate_score(outcomes: list[float]) -> Score:
    """Use Wilson for pass or fail cases. Use the bootstrap when repetitions give partial scores."""
    n = len(outcomes)
    if n == 0:
        return Score(0, None, None)
    if all(o in (0.0, 1.0) for o in outcomes):
        return Score(n, mean(outcomes), wilson_interval(int(sum(outcomes)), n), "wilson")
    return Score(n, mean(outcomes), bootstrap_ci(outcomes), "bootstrap")


@dataclass
class Scorecard:
    overall: Score
    by_task: dict[str, Score]
    skipped_cases: int            # Cases left out of the rates.
    evaluator_errors: int         # Executions where the evaluator broke.


def scorecard(records: list[Record]) -> Scorecard:
    outcomes = case_outcomes(records)
    task_of = {r.case_id: r.task for r in records}
    scored = {c: o for c, o in outcomes.items() if o is not None}
    by_task: dict[str, list[float]] = {}
    for c, o in scored.items():
        by_task.setdefault(task_of[c], []).append(o)
    return Scorecard(
        overall=rate_score(list(scored.values())),
        by_task={t: rate_score(v) for t, v in sorted(by_task.items())},
        skipped_cases=len(outcomes) - len(scored),
        evaluator_errors=sum(r.evaluator_error for r in records),
    )
