"""A report of one run that a person can send to someone else: Markdown or JSON.

It has the same numbers as the dashboard, each with its interval and number of cases, and a section that says how to
repeat the run. A reader who was not there can see what was tested, what was found and how sure we are.
"""

from dataclasses import asdict, is_dataclass

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import __version__
from .db import EvaluationRun, ReleaseDecision
from .failure_report import analyze_failures
from .robustness import robustness_report
from .rundata import load_run
from .summary import model_summary


def _plain(x):
    if is_dataclass(x) and not isinstance(x, type):
        return _plain(asdict(x))
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def build_report(factory: sessionmaker, run_id: str, model: str | None = None) -> dict:
    rd = load_run(factory, run_id)
    models = [rd.pick_model(model)] if model else rd.models
    with factory() as s:
        run = s.get(EvaluationRun, run_id)
        decisions = [d for d in s.scalars(select(ReleaseDecision).where(ReleaseDecision.run_id == run_id).order_by(ReleaseDecision.id))]
        meta = {"run_id": run_id, "name": run.name, "status": run.status, "dataset": rd.dataset, "dataset_version": rd.dataset_hash,
                "created_at": run.created_at.isoformat() + "Z", "software_version": run.software_version, "report_made_with": __version__,
                "config": run.config_snapshot, "cost_usd": run.total_cost_usd, "judge_cost_usd": run.judge_cost_usd}
        dec = [{"model": d.model, "policy": d.policy_name, "status": d.status, "allowed": d.allowed, "checks": d.checks, "warnings": d.warnings,
                "baseline_run_id": d.baseline_run_id, "at": d.created_at.isoformat() + "Z"} for d in decisions if d.model in models]
    out = {"meta": meta, "models": {}, "decisions": dec}
    for m in models:
        recs = rd.for_model(m)
        entry = {"summary": _plain(model_summary(recs))}
        failures = analyze_failures(recs)
        if failures["failed_cases"]:
            entry["failures"] = _plain(failures)
        rob = robustness_report(recs)
        if rob.pairs:
            entry["robustness"] = _plain(rob)
        out["models"][m] = entry
    return out


def _pct(x, d=1):
    return "–" if x is None else f"{x * 100:.{d}f}%"


def _ci(ci):
    return "" if not ci else f"{ci[0] * 100:.1f} to {ci[1] * 100:.1f}%"


def _ms(v):
    return "–" if v is None else (f"{v / 1000:.2f} s" if v >= 1000 else f"{v:.0f} ms")


def to_markdown(r: dict) -> str:
    m = r["meta"]
    L = [f"# Run report: {m['name'] or m['run_id']}", "",
         f"Run `{m['run_id']}` · dataset `{m['dataset']}` (version `{m['dataset_version']}`) · {m['created_at'][:16].replace('T', ' ')} UTC · state: {m['status']}", "",
         "> Every rate comes with a 95% interval and its number of cases. A wide interval means that too few cases were tested to be sure. "
         "A failed model call counts as a failed case. Cases that were skipped, or whose evaluator broke, are left out and counted below.", ""]
    for model, e in r["models"].items():
        s = e["summary"]
        L += [f"## Model `{model}`", "", "### Pass rate", "", "| Group | Cases | Pass rate | 95% interval |", "|---|---:|---:|---|",
              f"| **All cases** | {s['overall']['n']} | **{_pct(s['overall']['rate'])}** | {_ci(s['overall']['ci'])} |"]
        for task, v in sorted(s["by_task"].items(), key=lambda kv: kv[1]["rate"] or 0):
            L.append(f"| {task.replace('_', ' ')} | {v['n']} | {_pct(v['rate'])} | {_ci(v['ci'])} |")
        left = s["left_out"]
        if left["skipped_cases"] or left["evaluator_errors"]:
            L += ["", f"Left out of these rates: {left['skipped_cases']} skipped case(s) and {left['evaluator_errors']} result(s) where an evaluator broke."]
        if s["weak_slices"]:
            L += ["", "### Weak areas", "", "Groups of cases that are clearly weaker than the rest (false discovery rate controlled):", ""]
            for w in s["weak_slices"]:
                L.append(f"- {w['dimension'].replace('_', ' ')} = **{str(w['value']).replace('_', ' ')}**: {_pct(w['rate'], 0)} pass against {_pct(w['rest_rate'], 0)} on the other cases (n={w['n']}, p={w['p_value']:.4f})")
        o = s["operations"]
        cost = "unknown" if o["cost_per_success_usd"] is None else f"${o['cost_per_success_usd']:.4f}"
        L += ["", "### Speed, errors and cost", "", "| Measure | Value |", "|---|---:|", f"| Latency P50 / P95 / P99 | {_ms(o['latency_ms']['50'])} / {_ms(o['latency_ms']['95'])} / {_ms(o['latency_ms']['99'])} |",
              f"| Calls that failed | {_pct(o['call_failure_rate'])} |", f"| Calls retried | {_pct(o['retry_rate'])} |", f"| Cost per success | {cost} |"]
        if "failures" in e:
            f = e["failures"]
            L += ["", "### How cases failed", "", f"{f['failed_cases']} of {f['cases_with_a_result']} cases failed. One case can fail in more than one way.", "", "| Failure | Cases | Share | Meaning |", "|---|---:|---:|---|"]
            for lab in f["labels"]:
                L.append(f"| {lab['label'].replace('_', ' ')} | {lab['cases']} | {_pct(lab['share'], 0)} | {lab['meaning']} |")
            if f["priority"]:
                L += ["", "What to fix first (score = severity weight × frequency × exposure × detectability):", ""]
                for p in f["priority"][:5]:
                    L.append(f"{f['priority'].index(p) + 1}. {p['name'].replace('_', ' ')}: severity {p['severity']}, {p['frequency_pct']:.1f}% of cases, score {p['score']:.0f}")
            if f["clusters"]:
                L += ["", "Recurring patterns (they show what failures share, not their cause):", ""]
                for c in f["clusters"]:
                    L.append(f"- {c['size']} cases about {', '.join(c['terms'][:4])}. {c['follow_up']}")
        if "robustness" in e:
            b = e["robustness"]
            br = b["break_rate"]
            L += ["", "### Robustness", "", f"{b['pairs']} pairs of an original case and a changed copy that must give the same result. "
                  f"Pass rate {_pct(b['original_rate'])} on originals and {_pct(b['variant_rate'])} on copies. "
                  f"Break rate (passed the original, failed the copy): {br['k']} of {br['n']}" + (f" ({_ci(br['ci'])})" if br["ci"] else "") + "."]
            if b["worst_transform"]:
                L.append(f"Weakest change: {b['worst_transform']['name'].replace('_', ' ')} ({_pct(b['worst_transform']['variant_rate'], 0)} pass).")
        if s.get("calibration"):
            c = s["calibration"]
            L += ["", "### Calibration", "", f"{c['n']} answers with a stated confidence. Accuracy {_pct(c['accuracy'], 0)}, average confidence {_pct(c['mean_confidence'], 0)}, "
                  f"Brier score {c['brier']:.3f}, AUROC {c['auroc']:.2f}." if c["brier"] is not None and c["auroc"] is not None else f"{c['n']} answers with a stated confidence."]
        L.append("")
    if r["decisions"]:
        L += ["## Release decisions", ""]
        for d in r["decisions"]:
            L += [f"### {d['status'].upper()}: `{d['model']}` against policy “{d['policy']}” ({'release may go on' if d['allowed'] else 'release blocked'})", "",
                  "| Rule | Result | Required | Observed |", "|---|---|---|---|"]
            for c in d["checks"]:
                L.append(f"| {c['rule'].replace('_', ' ')} | {c['status']} | {c['required']} | {c['observed']} |")
            L.append("")
    cfg = m["config"]
    L += ["## How to repeat this run", "", f"- dataset `{m['dataset']}`, version `{m['dataset_version']}` (a version never changes)",
          f"- models: {', '.join(cfg.get('models', []))}", f"- options: `{cfg.get('options')}`", f"- run with trustbench {m['software_version']}",
          f"- cost: ${m['cost_usd']:.4f}" + (f" (judge ${m['judge_cost_usd']:.4f})" if m["judge_cost_usd"] else ""), "",
          "*Passing a test suite means meeting these tests under these conditions. It does not prove that a system is safe or reliable in general.*", ""]
    return "\n".join(L)
