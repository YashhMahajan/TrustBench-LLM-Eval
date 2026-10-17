"""Commands for comparing runs and deciding on a release."""

from pathlib import Path

import typer
from rich.table import Table

from . import baselines
from .cli import app
from .cli_common import console, get_factory
from .comparison import compare_runs
from .gate import evaluate_gate
from .operations import operations
from .policy import TEMPLATE, PolicyError, load_policy
from .rundata import load_run
from .slices import regression_scan

VERDICT_TEXT = {
    "meaningful_improvement": "[green]better[/green]",
    "improvement_below_threshold": "[green]slightly better[/green]",
    "equivalent": "no real change",
    "inconclusive": "[yellow]cannot tell[/yellow]",
    "regression_below_threshold": "[red]slightly worse[/red]",
    "meaningful_regression": "[red]worse[/red]",
}
STATUS_TEXT = {"pass": "[green]pass[/green]", "fail": "[red]fail[/red]",
               "inconclusive": "[yellow]inconclusive[/yellow]", "incomplete": "[yellow]incomplete[/yellow]"}


def _pp(x):
    return "-" if x is None else f"{x:+.1%}"


@app.command()
def compare(
    candidate: str = typer.Argument(..., help="Run id (or baseline name) to test"),
    baseline: str = typer.Option(..., "--baseline", "-b", help="Run id or baseline name to compare with"),
    model: str = typer.Option(None, help="Candidate model, if its run has several"),
    baseline_model: str = typer.Option(None, help="Baseline model, if its run has several"),
    min_effect: float = typer.Option(0.01, help="Smallest change in pass rate that matters"),
    slices: bool = typer.Option(False, "--slices", help="Also look for slices that got worse"),
):
    """Compare a candidate with a baseline on the same cases."""
    factory = get_factory()
    try:
        b_id, b_model = baselines.resolve(factory, baseline)
        c_id, c_model = baselines.resolve(factory, candidate)
        base, cand = load_run(factory, b_id), load_run(factory, c_id)
        b_model, c_model = baseline_model or b_model, model or c_model
        cmp = compare_runs(base, cand, b_model, c_model, min_effect)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    t = Table(title=f"{c_id} ({cand.pick_model(c_model)}) against {b_id} ({base.pick_model(b_model)})")
    for col in ["Group", "Cases", "Baseline", "Candidate", "Change", "95% interval", "p-value", "Verdict"]:
        t.add_column(col)
    for d in [cmp.overall, *cmp.by_task.values()]:
        t.add_row(d.label, str(d.n), "-" if d.baseline is None else f"{d.baseline:.1%}",
                  "-" if d.candidate is None else f"{d.candidate:.1%}", _pp(d.diff),
                  "-" if d.ci is None else f"{d.ci[0]:+.1%} to {d.ci[1]:+.1%}",
                  "-" if d.p_value is None else f"{d.p_value:.3f}", VERDICT_TEXT[d.verdict])
    console.print(t)
    console.print(f"[dim]Test: {cmp.overall.method}. A change counts only past {min_effect:.1%}.[/dim]")

    ob, oc = operations(base.for_model(b_model)), operations(cand.for_model(c_model))
    ops = Table(title="Latency, errors and cost")
    for col in ["Measure", "Baseline", "Candidate"]:
        ops.add_column(col)
    ms = lambda v: "-" if v is None else f"{v / 1000:.2f} s"
    ops.add_row("P50 latency", ms(ob.latency_ms[50]), ms(oc.latency_ms[50]))
    ops.add_row("P95 latency", ms(ob.latency_ms[95]), ms(oc.latency_ms[95]))
    ops.add_row("Call failure rate", "-" if ob.call_failure_rate is None else f"{ob.call_failure_rate:.1%}",
                "-" if oc.call_failure_rate is None else f"{oc.call_failure_rate:.1%}")
    cost = lambda v: "unknown" if v is None else f"${v:.4f}"
    ops.add_row("Cost per success", cost(ob.cost_per_success_usd), cost(oc.cost_per_success_usd))
    console.print(ops)

    if slices:
        flagged = [s for s in regression_scan(base.for_model(b_model), cand.for_model(c_model)) if s.regression]
        if flagged:
            st = Table(title="Slices that got worse (false discovery rate controlled)")
            for col in ["Slice", "Cases", "Baseline", "Candidate", "Change", "p-value"]:
                st.add_column(col)
            for s in flagged:
                st.add_row(f"{s.dimension} = {s.value}", str(s.n), f"{s.baseline:.1%}", f"{s.candidate:.1%}",
                           _pp(s.diff), f"{s.p_value:.4f}")
            console.print(st)
        else:
            console.print("No slice got clearly worse.")
    for w in cmp.warnings:
        console.print(f"[yellow]{w}[/yellow]")


gate_app = typer.Typer(help="Decide if a run may be released.", no_args_is_help=True)
app.add_typer(gate_app, name="gate")


@gate_app.command("check")
def gate_check(
    run: str = typer.Argument(..., help="Run id (or baseline name) to check"),
    policy: Path = typer.Option(Path("policy.yaml"), help="Policy file"),
    baseline: str = typer.Option(None, "--baseline", "-b", help="Run id or baseline name for regression rules"),
    model: str = typer.Option(None, help="Model to check, if the run has several"),
    baseline_model: str = typer.Option(None),
):
    """Check a run against a policy. Exit code 0 means the release may go on. Exit code 1 means it may not."""
    factory = get_factory()
    try:
        pol = load_policy(policy)
        run_id, run_model = baselines.resolve(factory, run)
        cand = load_run(factory, run_id)
        model = model or run_model
        base_ref = baselines.resolve(factory, baseline) if baseline else None
        base = load_run(factory, base_ref[0]) if base_ref else None
        baseline_model = baseline_model or (base_ref[1] if base_ref else None)
        decision = evaluate_gate(pol, cand, model, base, baseline_model)
    except (ValueError, PolicyError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    t = Table(title=f"Gate '{pol.name}' for run {run_id} ({cand.pick_model(model)})")
    for col in ["Rule", "Result", "Required", "Observed"]:
        t.add_column(col)
    for c in decision.checks:
        t.add_row(c.rule, STATUS_TEXT[c.status], c.required, c.observed + (f"\n[dim]{c.evidence}[/dim]" if c.evidence else ""))
    console.print(t)
    for w in decision.warnings:
        console.print(f"[yellow]{w}[/yellow]")
    did = baselines.save_decision(factory, run_id, cand.pick_model(model),
                                  (base_ref[0], baseline_model or base_ref[1]) if base_ref else None, pol, decision)
    console.print(f"Decision: {STATUS_TEXT[decision.status]}. Release {'may go on' if decision.allowed else 'is blocked'}. "
                  f"[dim](saved as decision {did})[/dim]")
    raise typer.Exit(0 if decision.allowed else 1)


baseline_app = typer.Typer(help="Name a run as a baseline.", no_args_is_help=True)
app.add_typer(baseline_app, name="baseline")


@baseline_app.command("set")
def baseline_set(name: str, run_id: str, model: str = typer.Option(None, help="Model of the run to use")):
    """Use a run as the baseline called NAME. An old baseline with this name is replaced."""
    try:
        baselines.set_baseline(get_factory(), name, run_id, model)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Baseline '{name}' is now run {run_id}.")


@baseline_app.command("list")
def baseline_list():
    t = Table()
    for col in ["Name", "Run", "Model", "Set at"]:
        t.add_column(col)
    for b in baselines.list_baselines(get_factory()):
        t.add_row(b.name, b.run_id, b.model or "-", b.created_at.strftime("%Y-%m-%d %H:%M"))
    console.print(t)


policy_app = typer.Typer(help="Release policy files.", no_args_is_help=True)
app.add_typer(policy_app, name="policy")


@policy_app.command("init")
def policy_init(out: Path = typer.Option(Path("policy.yaml")), force: bool = typer.Option(False, "--force")):
    """Write a policy file with comments. Edit it, then use 'trustbench gate check'."""
    if out.exists() and not force:
        console.print(f"[red]{out} exists.[/red] Use --force to replace it.")
        raise typer.Exit(1)
    out.write_text(TEMPLATE)
    console.print(f"Wrote {out}.")
