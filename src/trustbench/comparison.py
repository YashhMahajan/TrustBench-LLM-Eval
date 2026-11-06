"""Compare a candidate with a baseline on the same cases.

The code pairs the cases by id. We test the differences, not the two rates alone,
because both systems answered the same questions.
"""

from dataclasses import dataclass, field

from .rundata import Record, RunData, case_outcomes
from .stats import mcnemar_exact, mean, paired_binary_ci, paired_bootstrap, paired_permutation_p, verdict

MIN_PAIRS = 30


@dataclass
class Delta:
    label: str
    n: int                                   # Paired cases.
    baseline: float | None
    candidate: float | None
    diff: float | None                       # Candidate minus baseline.
    ci: tuple[float, float] | None
    p_value: float | None                    # Two-sided. Small means the difference is unlikely to be chance.
    method: str
    verdict: str
    candidate_only_pass: int | None = None   # Cases only the candidate passed. Binary data only.
    baseline_only_pass: int | None = None


@dataclass
class Comparison:
    overall: Delta
    by_task: dict[str, Delta]
    paired_cases: int
    unpaired_cases: int                      # Cases missing or unscored in one of the two runs.
    warnings: list[str] = field(default_factory=list)


def _delta(label: str, base: list[float], cand: list[float], min_effect: float, n_boot: int) -> Delta:
    n = len(base)
    if n == 0:
        return Delta(label, 0, None, None, None, None, None, "none", "inconclusive")
    binary = all(x in (0.0, 1.0) for x in base + cand)
    if binary:
        diff = mean(cand) - mean(base)
        lo, hi = paired_binary_ci(base, cand)
    else:
        diff, lo, hi = paired_bootstrap(base, cand, n_boot=n_boot)
    if binary:
        a_only = sum(1 for x, y in zip(base, cand) if x == 1 and y == 0)
        b_only = sum(1 for x, y in zip(base, cand) if x == 0 and y == 1)
        p, method = mcnemar_exact(a_only, b_only), "mcnemar"
    else:
        a_only = b_only = None
        p, method = paired_permutation_p(base, cand), "permutation"
    return Delta(label, n, mean(base), mean(cand), diff, (lo, hi), p, method, verdict((lo, hi), min_effect),
                 b_only, a_only)


def compare_records(baseline: list[Record], candidate: list[Record], min_effect: float = 0.01,
                    n_boot: int = 4000) -> Comparison:
    base, cand = case_outcomes(baseline), case_outcomes(candidate)
    task_of = {r.case_id: r.task for r in baseline + candidate}
    shared = sorted(c for c in base.keys() & cand.keys() if base[c] is not None and cand[c] is not None)
    unpaired = len(base.keys() | cand.keys()) - len(shared)
    warnings = []
    if unpaired:
        warnings.append(f"{unpaired} case(s) are in only one run or have no score, so they are left out.")
    if 0 < len(shared) < MIN_PAIRS:
        warnings.append(f"Only {len(shared)} paired cases. Differences smaller than about 20 points cannot be seen.")
    tasks: dict[str, list[str]] = {}
    for c in shared:
        tasks.setdefault(task_of[c], []).append(c)
    return Comparison(
        overall=_delta("overall", [base[c] for c in shared], [cand[c] for c in shared], min_effect, n_boot),
        by_task={t: _delta(t, [base[c] for c in cs], [cand[c] for c in cs], min_effect, n_boot)
                 for t, cs in sorted(tasks.items())},
        paired_cases=len(shared), unpaired_cases=unpaired, warnings=warnings)


def compare_runs(baseline: RunData, candidate: RunData, baseline_model: str | None = None,
                 candidate_model: str | None = None, min_effect: float = 0.01) -> Comparison:
    c = compare_records(baseline.for_model(baseline_model), candidate.for_model(candidate_model), min_effect)
    if (baseline.dataset, baseline.dataset_hash) != (candidate.dataset, candidate.dataset_hash):
        c.warnings.insert(0, "The dataset versions differ. Cases with the same id may not be the same test.")
    for name, run in (("baseline", baseline), ("candidate", candidate)):
        if run.status != "complete":
            c.warnings.append(f"The {name} run is {run.status}.")
    return c
