"""The release gate. It turns results and a policy into a decision.

Four statuses:
  pass          every rule is met, with enough evidence
  fail          at least one rule is clearly broken
  inconclusive  the evidence is too weak to say
  incomplete    the run did not finish, or the evaluators broke

If a rule is clearly broken the status is fail, even when the run is also incomplete.
Weak evidence is never read as a pass.
"""

from dataclasses import asdict, dataclass, field

from .comparison import compare_records
from .operations import operations
from .policy import ReleasePolicy
from .rundata import Record, RunData, case_outcomes
from .scorecard import rate_score, scorecard
from .stats import bootstrap_ci, percentile, wilson_interval

MIN_LATENCY_SAMPLES = 20


@dataclass
class Check:
    rule: str
    status: str        # pass | fail | inconclusive | incomplete
    required: str
    observed: str
    evidence: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    status: str
    checks: list[Check]
    allowed: bool                    # True when the release may go on (exit code 0).
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "allowed": self.allowed, "warnings": self.warnings,
                "checks": [c.to_dict() for c in self.checks]}


def _floor_check(rule: str, floor: float, n: int, rate: float | None, ci, use_bound: bool, min_n: int) -> Check:
    required = f"at least {floor:.1%}" + (" (low end of the 95% interval)" if use_bound else "")
    if rate is None or n == 0:
        return Check(rule, "inconclusive", required, "no scored cases")
    observed = f"{rate:.1%} (95% interval {ci[0]:.1%} to {ci[1]:.1%}), n={n}"
    if n < min_n:
        return Check(rule, "inconclusive", required, observed, f"Only {n} case(s). This rule needs {min_n}.")
    if use_bound:
        if ci[0] >= floor:
            return Check(rule, "pass", required, observed)
        if ci[1] < floor:
            return Check(rule, "fail", required, observed, "The whole interval is below the minimum.")
        return Check(rule, "inconclusive", required, observed, "The interval crosses the minimum. More cases are needed.")
    return Check(rule, "pass" if rate >= floor else "fail", required, observed)


def _regression_check(rule: str, tol: float, delta) -> Check:
    required = f"fall by at most {tol:.1%} from the baseline"
    if delta is None or delta.n == 0:
        return Check(rule, "inconclusive", required, "no paired cases")
    observed = f"change {delta.diff:+.1%} (95% interval {delta.ci[0]:+.1%} to {delta.ci[1]:+.1%}), n={delta.n}"
    if delta.ci[1] < -tol:
        return Check(rule, "fail", required, observed, "The whole interval is beyond the allowed fall.")
    if delta.ci[0] >= -tol:
        return Check(rule, "pass", required, observed)
    return Check(rule, "inconclusive", required, observed, "The interval crosses the allowed fall. More cases are needed.")


def _ceiling_check(rule: str, limit: float, values: list[float], fmt, stat, min_n: int) -> Check:
    """For latency: pass when the high end of the interval is under the limit."""
    required = f"at most {fmt(limit)}"
    if len(values) < min_n:
        return Check(rule, "inconclusive", required, f"{len(values)} calls", f"Need {min_n} successful calls.")
    point = stat(values)
    lo, hi = bootstrap_ci(values, stat=stat, n_boot=1000)
    observed = f"{fmt(point)} (95% interval {fmt(lo)} to {fmt(hi)}), n={len(values)}"
    if hi <= limit:
        return Check(rule, "pass", required, observed)
    if lo > limit:
        return Check(rule, "fail", required, observed, "The whole interval is above the limit.")
    return Check(rule, "inconclusive", required, observed, "The interval crosses the limit.")


def evaluate_gate(policy: ReleasePolicy, candidate: RunData, model: str | None = None,
                  baseline: RunData | None = None, baseline_model: str | None = None) -> Decision:
    records = candidate.for_model(model)
    card = scorecard(records)
    ops = operations(records)
    checks: list[Check] = []
    warnings: list[str] = []

    # 1. Did the evaluation finish?
    unfinished = sum(r.status in ("skipped", "pending") for r in records)
    problems = []
    if candidate.status != "complete":
        problems.append(f"the run is {candidate.status}")
    if unfinished:
        problems.append(f"{unfinished} execution(s) did not run")
    if card.evaluator_errors:
        problems.append(f"{card.evaluator_errors} execution(s) could not be scored because an evaluator broke")
    checks.append(Check("evaluation_complete", "incomplete" if problems else "pass", "the run finished and every result is scored",
                        "; ".join(problems) or "complete"))

    # 2. Enough cases?
    n = card.overall.n
    checks.append(Check("minimum_cases", "pass" if n >= policy.min_cases else "inconclusive",
                        f"at least {policy.min_cases} scored cases", f"{n} scored cases"))

    # 3. Pass rates.
    def floors(label: str, rule, score):
        if rule.minimum_score is not None:
            checks.append(_floor_check(f"{label}.minimum_score", rule.minimum_score, score.n, score.rate, score.ci,
                                       policy.confidence_bound and score.method == "wilson", rule.min_cases))

    floors("overall", policy.overall, card.overall)
    for task, rule in policy.dimensions.items():
        if task not in card.by_task:
            if rule.minimum_score is not None or rule.maximum_regression is not None:
                checks.append(Check(f"{task}.minimum_score", "inconclusive", "the task is in the run", "the task has no scored cases"))
            continue
        floors(task, rule, card.by_task[task])

    # 4. Regression against the baseline.
    wants_baseline = policy.overall.maximum_regression is not None or any(
        r.maximum_regression is not None for r in policy.dimensions.values())
    if wants_baseline:
        if baseline is None:
            checks.append(Check("regression", "inconclusive", "a baseline to compare with", "no baseline",
                                "Set one with 'trustbench baseline set' or use --baseline."))
        else:
            cmp = compare_records(baseline.for_model(baseline_model), records, policy.min_effect)
            warnings += cmp.warnings
            if policy.overall.maximum_regression is not None:
                checks.append(_regression_check("overall.maximum_regression", policy.overall.maximum_regression, cmp.overall))
            for task, rule in policy.dimensions.items():
                if rule.maximum_regression is not None:
                    checks.append(_regression_check(f"{task}.maximum_regression", rule.maximum_regression, cmp.by_task.get(task)))

    # 5. Critical cases.
    if policy.critical_failures_allowed is not None:
        outcomes = case_outcomes(records)
        critical = {r.case_id for r in records if r.critical}
        failed = sorted(c for c in critical if outcomes.get(c) is not None and outcomes[c] < 1.0)
        required = f"at most {policy.critical_failures_allowed} failed critical case(s)"
        if not critical:
            checks.append(Check("critical_failures", "inconclusive", required, "no critical cases in this run",
                                'Mark cases with "critical": true in their metadata.'))
        else:
            ok = len(failed) <= policy.critical_failures_allowed
            checks.append(Check("critical_failures", "pass" if ok else "fail", required,
                                f"{len(failed)} of {len(critical)} failed", ", ".join(failed[:10])))

    # 6. Operations.
    if policy.latency_p95_max_seconds is not None:
        lat = [r.latency_ms / 1000 for r in records if r.status == "done" and r.latency_ms is not None]
        checks.append(_ceiling_check("latency_p95", policy.latency_p95_max_seconds, lat, lambda v: f"{v:.2f} s",
                                     lambda xs: percentile(xs, 95), MIN_LATENCY_SAMPLES))
    if policy.cost_per_success_max_usd is not None:
        c = ops.cost_per_success_usd
        required = f"at most ${policy.cost_per_success_max_usd:.4f}"
        if c is None:
            checks.append(Check("cost_per_success", "inconclusive", required, "unknown",
                                "No price is known for this model. Set TRUSTBENCH_PRICES."))
        else:
            if ops.priced_share is not None and ops.priced_share < 1:
                warnings.append(f"Only {ops.priced_share:.0%} of calls had a price, so the cost is too low.")
            checks.append(Check("cost_per_success", "pass" if c <= policy.cost_per_success_max_usd else "fail", required, f"${c:.4f}"))
    if policy.call_failure_rate_max is not None:
        required = f"at most {policy.call_failure_rate_max:.1%}"
        if ops.calls == 0:
            checks.append(Check("call_failure_rate", "inconclusive", required, "no calls"))
        else:
            failed_calls = round(ops.call_failure_rate * ops.calls)
            lo, hi = wilson_interval(failed_calls, ops.calls)
            observed = f"{ops.call_failure_rate:.1%} (95% interval {lo:.1%} to {hi:.1%}), n={ops.calls}"
            status = "pass" if hi <= policy.call_failure_rate_max else "fail" if lo > policy.call_failure_rate_max else "inconclusive"
            checks.append(Check("call_failure_rate", status, required, observed))

    statuses = {c.status for c in checks}
    status = ("fail" if "fail" in statuses else "incomplete" if "incomplete" in statuses
              else "inconclusive" if "inconclusive" in statuses else "pass")
    allowed = status == "pass" or (status == "inconclusive" and policy.on_insufficient_evidence == "allow")
    return Decision(status, checks, allowed, warnings)
