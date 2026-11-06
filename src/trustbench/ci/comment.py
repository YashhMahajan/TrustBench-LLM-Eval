"""The pull request comment. Pure functions: results in, Markdown out.

Treat everything from a model or from your test data as untrusted text. `safe` escapes it before it goes into
the comment. So a model cannot add a link, an image, HTML or an @mention to your pull request.
"""

import re

from .. import __version__
from ..comparison import compare_runs
from ..edd.costmodel import cost_report
from ..failures import collect_failures, label_counts, TAXONOMY
from ..gate import Decision
from ..operations import operations
from ..policy import ReleasePolicy
from ..rundata import RunData
from ..scorecard import scorecard
from ..slices import regression_scan

MARKER = "<!-- trustbench-report -->"
LIMIT = 65_536                 # GitHub rejects a comment longer than this.
DEFAULT_CAP = 60_000           # We stay well under it.
STATUS = {"pass": "✅ pass", "fail": "❌ fail", "inconclusive": "⚠️ inconclusive", "incomplete": "⚠️ incomplete"}
VERDICT = {"meaningful_improvement": "better", "improvement_below_threshold": "slightly better", "equivalent": "no real change",
           "inconclusive": "cannot tell", "regression_below_threshold": "slightly worse", "meaningful_regression": "worse"}


# What can turn text into something else in a GitHub comment: HTML (< > &), links and images ([ ]), mentions and
# emails (@), issue references (#), table cells (|), code (`), math ($) and escapes (\\). The code replaces each one once, in one pass.
# Bare web addresses become links by themselves. So the code also breaks up "://" and "www.".
_ESCAPES = {ord(c): f"&#{ord(c)};" for c in "&<>|`[]@#$\\"}
_AUTOLINK = re.compile(r"(?i)://|\bwww\.")


def safe(text, limit: int = 200) -> str:
    """Make untrusted text harmless in a Markdown table cell: one line, no HTML, link, image, mention, code or math."""
    t = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    if len(t) > limit:
        t = t[:limit].rstrip() + "…"                         # Cut first, then escape. Then the code never cuts an escape in half.
    return _AUTOLINK.sub(lambda m: "&#58;//" if m.group(0) == "://" else "www&#46;", t.translate(_ESCAPES))


def code(text, limit: int = 120) -> str:
    """Untrusted text for inside a `code span`. A code span shows its text as it is. So only the backtick and line breaks matter."""
    t = re.sub(r"\s+", " ", str(text if text is not None else "")).strip().replace("`", "'")
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def _pct(x) -> str:
    return "-" if x is None else f"{x:.1%}"


def _score(s) -> str:
    return "-" if s.rate is None else f"{s.rate:.1%} ({s.ci[0]:.1%} to {s.ci[1]:.1%}), n={s.n}"


def _usd(x) -> str:
    return "unknown" if x is None else f"${x:,.0f}"


def build_comment(candidate: RunData, model: str | None, *, baseline: RunData | None = None, baseline_model: str | None = None,
                  decision: Decision | None = None, policy: ReleasePolicy | None = None, min_effect: float = 0.01,
                  max_examples: int = 8, cap: int = DEFAULT_CAP) -> str:
    """The comment for one run. Shortens itself (examples first) to stay under `cap` characters."""
    records = candidate.for_model(model)
    model = candidate.pick_model(model)
    base_records = baseline.for_model(baseline_model) if baseline else None
    cmp = compare_runs(baseline, candidate, baseline_model, model, min_effect) if baseline else None
    card, ops = scorecard(records), operations(records)
    failures = collect_failures(records)
    counts = label_counts(failures, len({r.case_id for r in records}))
    regressed = [s for s in regression_scan(base_records, records) if s.regression] if base_records else []
    cost = cost_report(policy.cost_model, records) if policy and policy.cost_model else None

    def render(examples: int) -> str:
        head = STATUS[decision.status] if decision else "results"
        out = [MARKER, f"## TrustBench: {head}", "",
               f"Run `{code(candidate.run_id)}` · `{code(model)}` · dataset `{code(candidate.dataset)}` · {card.overall.n} scored cases"
               + (f" · policy `{code(policy.name)}`" if policy else "")]
        if decision:
            out += ["", "### Release gate", "", "| Rule | Result | Required | Observed |", "|---|---|---|---|"]
            out += [f"| {safe(c.rule)} | {STATUS[c.status]} | {safe(c.required)} | {safe(c.observed)} |" for c in decision.checks]
            out += [f"\n> {safe(w, 300)}" for w in decision.warnings]
        out += ["", "### Pass rate", "", "| Group | Pass rate (95% interval) |" + (" Baseline | Change | Verdict |" if cmp else ""),
                "|---|---|" + ("---|---|---|" if cmp else "")]
        groups = [("overall", card.overall, cmp.overall if cmp else None)] + [
            (t, s, cmp.by_task.get(t) if cmp else None) for t, s in card.by_task.items()]
        for label, s, d in groups:
            row = f"| {safe(label)} | {_score(s)} |"
            if cmp:
                row += (f" {_pct(d.baseline)} | {d.diff * 100:+.1f} pts | {VERDICT[d.verdict]} |" if d and d.diff is not None else " - | - | - |")
            out.append(row)
        if cmp:
            out.append(f"\nCompared with run `{code(baseline.run_id)}` on {cmp.paired_cases} shared cases ({cmp.overall.method} test). "
                       f"A change counts past {min_effect:.1%}.")
        if regressed:
            out += ["", "### Slices that got worse", "", "| Slice | Cases | Baseline | Now |", "|---|---|---|---|"]
            out += [f"| {safe(s.dimension)} = {safe(s.value)} | {s.n} | {_pct(s.baseline)} | {_pct(s.candidate)} |" for s in regressed]
        out += ["", "### Cost and speed", "", "| Measure | Value |", "|---|---|",
                f"| Latency P50 / P95 | {_ms(ops.latency_ms[50])} / {_ms(ops.latency_ms[95])} |",
                f"| Call failure rate | {_pct(ops.call_failure_rate)} |",
                f"| Cost per success | {'unknown' if ops.cost_per_success_usd is None else f'${ops.cost_per_success_usd:.4f}'} |"]
        if cost:
            out.append(f"| Cost a year with mistakes | {_usd(cost.annual_cost)}"
                       + (f" (95% {_usd(cost.annual_ci[0])} to {_usd(cost.annual_ci[1])})" if cost.annual_ci else "") + " |")
            if cost.manual_cost is not None:
                out.append(f"| Manual process today | {_usd(cost.manual_cost)} |")
        if failures:
            out += ["", f"### How {len(failures)} case(s) failed", "", "| Pattern | Cases | Meaning |", "|---|---|---|"]
            out += [f"| {safe(c.label)} | {c.cases} | {safe(TAXONOMY.get(c.label, ''), 120)} |" for c in counts[:8]]
            if examples:
                out += ["", "| Case | Labels | Why |", "|---|---|---|"]
                out += [f"| {safe(f.case_id, 60)} | {safe(', '.join(f.labels), 80)} | {safe('; '.join(f.reasons[:2]) or f.output, 200)} |"
                        for f in failures[:examples]]
                if len(failures) > examples:
                    out.append(f"\n…and {len(failures) - examples} more. Run `trustbench failures {code(candidate.run_id)}` for all of them.")
        out += ["", f"<sub>TrustBench {__version__}. Intervals are 95%. A small number of cases gives a wide interval.</sub>"]
        return "\n".join(out)

    for examples in range(max_examples, -1, -1):
        text = render(examples)
        if len(text) <= cap:
            return text
    notice = "\n\n…The comment was cut to fit GitHub's size limit. See the report artifact for everything."
    return text[:cap - len(notice)] + notice


def _ms(v) -> str:
    return "-" if v is None else f"{v / 1000:.2f} s"
