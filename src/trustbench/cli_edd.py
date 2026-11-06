"""Commands for the EDD workflow: init, plugins."""

from pathlib import Path

import typer

from .cli import app
from .cli_common import console


@app.command()
def init(out: Path = typer.Option(Path("trustbench.yaml"), help="Where to write the file"),
         force: bool = typer.Option(False, "--force", help="Replace the file if it exists")):
    """Write a commented trustbench.yaml. Edit it, then run: trustbench run"""
    from .edd.config import TEMPLATE
    if out.exists() and not force:
        console.print(f"[red]{out} exists.[/red] Use --force to replace it.")
        raise typer.Exit(1)
    out.write_text(TEMPLATE)
    console.print(f"Wrote {out}. Next: edit the targets, then run [bold]trustbench run[/bold].")


@app.command()
def plugins():
    """List the evaluators from other packages and any plugin that failed to load."""
    from .edd.plugins import LOADED, PLUGIN_ERRORS, load_evaluator_plugins
    load_evaluator_plugins()
    if not LOADED and not PLUGIN_ERRORS:
        console.print("No evaluator plugins are installed. See edd/plugins.py to write one.")
    for name in LOADED:
        console.print(f"[green]loaded[/green]  {name}")
    for name, why in PLUGIN_ERRORS.items():
        console.print(f"[red]failed[/red]  {name}: {why}")
    raise typer.Exit(1 if PLUGIN_ERRORS else 0)


@app.command()
def cost(run: str = typer.Argument(..., help="Run id (or baseline name)"),
         policy: Path = typer.Option(Path("policy.yaml"), help="Policy file with a cost_model block"),
         model: str = typer.Option(None, help="Model to read, if the run has several"),
         as_json: bool = typer.Option(False, "--json", help="Print JSON")):
    """What the system costs per year, with its mistakes. Compared with the manual process and a perfect system."""
    import json
    from dataclasses import asdict
    from rich.table import Table
    from . import baselines
    from .cli_common import get_factory
    from .edd.costmodel import cost_report
    from .policy import PolicyError, load_policy
    from .rundata import load_run
    try:
        pol = load_policy(policy)
        if pol.cost_model is None:
            raise PolicyError(f"{policy} has no cost_model block. See 'trustbench policy init' for an example.")
        run_id, run_model = baselines.resolve(get_factory(), run)
        data = load_run(get_factory(), run_id)
        rep = cost_report(pol.cost_model, data.for_model(model or run_model))
    except (ValueError, PolicyError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(asdict(rep), indent=2))
        raise typer.Exit(0 if rep.status == "ok" else 1)
    usd = lambda v: "unknown" if v is None else f"${v:,.0f}"  # noqa: E731
    rng = lambda ci: "" if ci is None else f" (95% {usd(ci[0])} to {usd(ci[1])})"  # noqa: E731
    pct = lambda v, ci: "-" if v is None else f"{v:.1%} (95% {ci[0]:.1%} to {ci[1]:.1%})"  # noqa: E731
    t = Table(title=f"Yearly cost of run {run_id}")
    t.add_column("Item")
    t.add_column("Value")
    t.add_row("Misses (false negatives)", f"{pct(rep.fn_rate, rep.fn_ci)} of {rep.positives} positive cases")
    t.add_row("False alarms (false positives)", f"{pct(rep.fp_rate, rep.fp_ci)} of {rep.negatives} negative cases")
    t.add_row("This system", usd(rep.annual_cost) + rng(rep.annual_ci))
    if rep.status != "ok":
        t.add_row("Without the cost of running it", usd(rep.without_processing))
    t.add_row("Manual process today", usd(rep.manual_cost))
    t.add_row("Perfect system", usd(rep.perfect_cost))
    if rep.savings is not None:
        t.add_row("Saving against manual", usd(rep.savings) + rng(rep.savings_ci))
    console.print(t)
    for n in rep.notes:
        console.print(f"[yellow]{n}[/yellow]")
    if rep.status != "ok":
        console.print(f"[yellow]Incomplete: {rep.reason}[/yellow]")
        raise typer.Exit(1)


experiment_app = typer.Typer(help="The history of an EDD session: variants, hypotheses and what the evidence said.", no_args_is_help=True)
app.add_typer(experiment_app, name="experiment")


@experiment_app.command("record")
def experiment_record(name: str = typer.Argument(..., help="A name for this variant, for example v1"),
                      run: str = typer.Argument(..., help="Run id (or baseline name) that measured it"),
                      parent: str = typer.Option(None, "--parent", "-p", help="The variant this one changed"),
                      hypothesis: str = typer.Option("", "--hypothesis", "-h", help="What you expect the change to do, and why"),
                      model: str = typer.Option(None, help="Model of the run, if it has several")):
    """Name a run as a variant, with its parent and your hypothesis."""
    from .cli_common import get_factory
    from .edd import experiments
    try:
        experiments.record(get_factory(), name, run, parent, hypothesis, model)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Recorded experiment {name}" + (f" (changes {parent})" if parent else "") + ".")


@experiment_app.command("list")
def experiment_list():
    """Every variant in order, with its pass rate and hypothesis."""
    from rich.table import Table
    from .cli_common import get_factory
    from .edd import experiments
    rows = experiments.timeline(get_factory())
    if not rows:
        console.print("No experiments yet. Name a run with 'trustbench experiment record'.")
        return
    t = Table(title="Experiments")
    for col in ["Name", "Changes", "Run", "Pass rate", "Hypothesis"]:
        t.add_column(col)
    for r in rows:
        t.add_row(r.name, r.parent or "-", r.run_id, r.score.text, r.hypothesis or "-")
    console.print(t)


VERDICT_WORDS = {"supported": "[green]supported[/green]", "refuted": "[red]refuted[/red]", "mixed": "[yellow]mixed[/yellow]",
                 "no_effect": "no real effect", "cannot_tell": "[yellow]cannot tell[/yellow]"}


@experiment_app.command("compare")
def experiment_compare(name: str = typer.Argument(..., help="The variant to judge. TrustBench compares it with its parent."),
                       policy: Path = typer.Option(Path("policy.yaml"), help="Policy file (its cost_model adds dollars)"),
                       min_effect: float = typer.Option(0.01, help="Smallest change in pass rate that matters")):
    """Does the evidence support the hypothesis? Compares a variant with its parent."""
    from rich.table import Table
    from .cli_common import get_factory
    from .cli_stats import VERDICT_TEXT
    from .edd import experiments
    from .policy import PolicyError, load_policy
    cost_model = None
    if policy.exists():
        try:
            cost_model = load_policy(policy).cost_model
        except PolicyError as e:
            console.print(f"[yellow]Ignoring {policy}: {e}[/yellow]")
    try:
        rep = experiments.compare_with_parent(get_factory(), name, min_effect, cost_model)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{rep.name}[/bold] against its parent [bold]{rep.parent}[/bold]")
    if rep.hypothesis:
        console.print(f"Hypothesis: {rep.hypothesis}")
    t = Table()
    for col in ["Group", "Cases", "Parent", "This variant", "Change", "95% interval", "Verdict"]:
        t.add_column(col)
    for d in [rep.comparison.overall, *rep.comparison.by_task.values()]:
        t.add_row(d.label, str(d.n), "-" if d.baseline is None else f"{d.baseline:.1%}",
                  "-" if d.candidate is None else f"{d.candidate:.1%}", "-" if d.diff is None else f"{d.diff * 100:+.1f} pts",
                  "-" if d.ci is None else f"{d.ci[0]:+.1%} to {d.ci[1]:+.1%}", VERDICT_TEXT[d.verdict])
    console.print(t)
    for s in rep.regressed_slices:
        console.print(f"[red]Slice got worse:[/red] {s.dimension} = {s.value} ({s.baseline:.1%} to {s.candidate:.1%}, n={s.n})")
    if rep.cost:
        p, c = rep.cost
        money = lambda r: "unknown" if r.annual_cost is None else f"${r.annual_cost:,.0f}"  # noqa: E731
        console.print(f"Yearly cost: parent {money(p)}, this variant {money(c)}")
    if rep.prompt_diff:
        console.print("\n[bold]Prompt change[/bold]")
        console.print(rep.prompt_diff, markup=False)
    for w in rep.warnings:
        console.print(f"[yellow]{w}[/yellow]")
    console.print(f"\nHypothesis: {VERDICT_WORDS[rep.verdict]}")
