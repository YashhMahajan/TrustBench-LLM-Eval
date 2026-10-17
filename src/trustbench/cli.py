import typer
from rich.console import Console
from rich.table import Table

from pathlib import Path

from . import annotations, quality, report
from .judge_validation import validate_judge
from .config import database_url, detect_models
from .datasets import DatasetError, import_dataset, load_cases, resolve_path
from .db import init_db, make_engine
from .engine import RunOptions, rescore, run_eval

app = typer.Typer(help="TrustBench: evaluate LLMs. Set an API key and run.", no_args_is_help=True)
console = Console()


def _factory():
    try:
        return init_db(make_engine(database_url()))
    except Exception as e:
        console.print(f"[red]Cannot reach the database:[/red] {e}\n"
                      "Start it with 'docker compose up -d', or set TRUSTBENCH_DATABASE_URL.")
        raise typer.Exit(1)


def _show(factory, run_id: str, batch: int | None = None) -> None:
    rows = report.case_rows(factory, run_id, batch)
    t = Table(title=f"Run {run_id}")
    for col in ["Model", "Cases", "Scored", "Passed", "Pass rate", "Call failures", "Skipped", "Mean latency", "Cost"]:
        t.add_column(col)
    for m in report.summarize(rows):
        t.add_row(m["model"], str(m["total"]), str(m["scored"]), str(m["passed"]),
                  f"{m['pass_rate']:.0%}" if m["pass_rate"] is not None else "-",
                  str(m["failed_calls"]), str(m["skipped"]),
                  f"{m['mean_latency_ms']:.0f} ms" if m["mean_latency_ms"] is not None else "-",
                  f"${m['cost']:.4f}" if m["cost"] else "-")
    console.print(t)
    run = report.get_run(factory, run_id)
    if run.status != "complete":
        console.print(f"[yellow]Run status: {run.status}. Some cases did not finish.[/yellow]")


@app.command()
def run(
    dataset: str = typer.Option("smoke", help="Built-in suite name or path to .jsonl/.json/.csv"),
    model: list[str] = typer.Option(None, "--model", "-m", help="provider:model. Repeat for more models."),
    reps: int = typer.Option(1, help="Repetitions for each case"),
    concurrency: int = typer.Option(4),
    retries: int = typer.Option(2),
    timeout: float = typer.Option(60.0, help="Seconds for each model call"),
    budget: float = typer.Option(None, help="Stop starting new calls after this many USD"),
    name: str = typer.Option(None, help="A label for the run"),
    judge: str = typer.Option(None, help="provider:model that grades open answers"),
):
    """Run a dataset against your models. Without --model, use every API key found."""
    specs = model or detect_models()
    if not specs:
        console.print("[red]No model found.[/red] Set ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY, "
                      "or use --model mock:smoke to try it without a key.")
        raise typer.Exit(1)
    try:
        path = resolve_path(dataset)
        cases = load_cases(path)
    except DatasetError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    issues = quality.check(cases)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        console.print(f"[red]The dataset has {len(errors)} error(s).[/red] Run 'trustbench check {dataset}' to see them.")
        raise typer.Exit(1)
    if issues:
        console.print(f"[yellow]{len(issues)} dataset warning(s). Run 'trustbench check {dataset}' to see them.[/yellow]")
    factory = _factory()
    with factory() as s:
        version = import_dataset(s, path.stem, cases)
    console.print(f"Dataset {version.name} version {version.content_hash}: {version.case_count} cases. "
                  f"Models: {', '.join(specs)}")
    try:
        run_id = run_eval(factory, version.id, specs,
                          RunOptions(repetitions=reps, concurrency=concurrency, retries=retries,
                                     timeout=timeout, budget_usd=budget, name=name, judge=judge))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _show(factory, run_id)


@app.command("rescore")
def rescore_cmd(run_id: str, judge: str = typer.Option(None, help="provider:model that grades open answers")):
    """Score the stored outputs of a run again. No model calls."""
    factory = _factory()
    try:
        batch = rescore(factory, run_id, judge)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"New scoring batch: {batch}")
    _show(factory, run_id, batch)



@app.command()
def results(run_id: str, failed: bool = typer.Option(False, "--failed", help="Show failures only")):
    """Show the result of each case."""
    factory = _factory()
    _show(factory, run_id)
    t = Table()
    for col in ["Model", "Case", "Result", "Detail"]:
        t.add_column(col)
    for r in report.case_rows(factory, run_id):
        if failed and r["passed"]:
            continue
        result = r["status"] if r["passed"] is None else ("pass" if r["passed"] else "fail")
        t.add_row(r["model"], r["case_id"], result, r["error"] or r["rationale"] or "")
    console.print(t)


@app.command()
def check(dataset: str):
    """Find problems in a dataset: duplicates, conflicts, missing references, personal data."""
    try:
        cases = load_cases(resolve_path(dataset))
    except DatasetError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    issues = quality.check(cases)
    if not issues:
        console.print(f"[green]{len(cases)} cases. No problems found.[/green]")
        return
    t = Table(title=f"{len(cases)} cases, {len(issues)} issue(s)")
    for col in ["Severity", "Code", "Cases", "Detail"]:
        t.add_column(col)
    for i in issues:
        t.add_row(i.severity, i.code, ", ".join(i.case_ids) or "-", i.message)
    console.print(t)
    if any(i.severity == "error" for i in issues):
        raise typer.Exit(1)


notes = typer.Typer(help="Human labels for answers. Used to check the judge.", no_args_is_help=True)
app.add_typer(notes, name="annotations")


@notes.command("export")
def annotations_export(run_id: str, out: Path = typer.Option(Path("annotations.csv"), help="CSV file to write")):
    """Write a CSV with every answer. Fill in the label column, then import it."""
    count = annotations.export_template(_factory(), run_id, out)
    console.print(f"Wrote {count} answers to {out}. Fill in the 'label' column.")


@notes.command("import")
def annotations_import(
    file: Path,
    annotator: str = typer.Option(..., help="Who labelled the answers"),
    dimension: str = typer.Option("correctness", help="What the label measures"),
):
    """Store the labels from a CSV."""
    try:
        stored, skipped = annotations.import_annotations(_factory(), file, annotator, dimension)
    except annotations.AnnotationError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Stored {stored} label(s). Skipped {skipped} empty row(s).")


@app.command("validate-judge")
def validate_judge_cmd(run_id: str, dimension: str = typer.Option("correctness", help="Label dimension to compare")):
    """Compare the judge grades of a run with human labels."""
    v = validate_judge(_factory(), run_id, dimension)
    t = Table(title=f"Judge validation, {v.n} labelled answers")
    t.add_column("Measure")
    t.add_column("Value")

    def fmt(x, pct=False):
        return "-" if x is None else (f"{x:.0%}" if pct else f"{x:.2f}")

    ci = f" (95% CI {v.exact_agreement_ci[0]:.0%} to {v.exact_agreement_ci[1]:.0%})" if v.exact_agreement_ci else ""
    t.add_row("Exact agreement", fmt(v.exact_agreement, True) + ci)
    t.add_row("Cohen's kappa", fmt(v.kappa))
    t.add_row("Weighted kappa", fmt(v.weighted_kappa))
    t.add_row("Pass/fail agreement", fmt(v.pass_agreement, True))
    t.add_row("False pass rate", fmt(v.false_pass_rate, True))
    t.add_row("False fail rate", fmt(v.false_fail_rate, True))
    t.add_row("Human agreement (alpha)", fmt(v.human_alpha))
    t.add_row("Grades the judge flagged", fmt(v.judge_review_rate, True))
    console.print(t)
    for w in v.warnings:
        console.print(f"[yellow]{w}[/yellow]")


@app.command("judge-bias")
def judge_bias_cmd(
    judge: str = typer.Option(..., help="provider:model of the judge to test"),
    dataset: str = typer.Option("factual", help="Dataset with reference answers"),
    max_cases: int = typer.Option(20, help="Use at most this many cases"),
):
    """Test a judge for position bias, length bias and sensitivity to wording."""
    import asyncio

    from .engine import make_judge
    from .judge import RUBRICS
    from .judge_bias import run_bias_probes
    from .providers import get_provider

    try:
        cases = load_cases(resolve_path(dataset))
        j = make_judge(judge, get_provider)
    except (DatasetError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    r = asyncio.run(run_bias_probes(j, cases, RUBRICS["correctness"], max_cases))
    t = Table(title=f"Judge bias probes: {judge}")
    for col in ["Probe", "Result", "Ideal"]:
        t.add_column(col)

    def pct(x):
        return "-" if x is None else f"{x:.0%}"

    t.add_row("Position: pick changes with order", pct(r.position_inconsistency), "0%")
    t.add_row("Position: reference answer wins", pct(r.correct_pick_rate), "100%")
    t.add_row("Length: score change from padding", "-" if r.verbosity_mean_delta is None else f"{r.verbosity_mean_delta:+.2f}", "0")
    t.add_row("Wording: score change from rewording", "-" if r.wording_mean_abs_change is None else f"{r.wording_mean_abs_change:.2f}", "0")
    console.print(t)
    if r.errors:
        console.print(f"[yellow]{r.errors} probe(s) skipped because the judge reply was not readable.[/yellow]")


@app.command()
def show(run_id: str, case_id: str):
    """Explain the result of one case: the answer, each score and the reason for it."""
    details = report.case_detail(_factory(), run_id, case_id)
    if not details:
        console.print(f"[red]No case '{case_id}' in run {run_id}.[/red]")
        raise typer.Exit(1)
    for d in details:
        console.rule(f"{d['model']}  (repetition {d['repetition']}, {d['status']})")
        console.print(f"[bold]Input:[/bold] {d['input']}")
        console.print(f"[bold]Expected:[/bold] {d['expected'] or '-'}")
        console.print(f"[bold]Output:[/bold] {d['output'] if d['output'] is not None else '-'}")
        for a in d["attempts"]:
            if not a["ok"]:
                console.print(f"[yellow]Attempt {a['attempt']} failed: {a['error']}[/yellow]")
        for r in d["results"]:
            mark = "[green]pass[/green]" if r["passed"] else "[red]fail[/red]"
            console.print(f"{mark} {r['evaluator']} ({r['rubric_version']}), score {r['score']:.2f}: {r['rationale']}")
            if r["details"].get("human_review"):
                console.print("  [yellow]A person should check this result.[/yellow]")
            if r["details"].get("error"):
                console.print("  [yellow]This is an evaluator problem, not a model failure.[/yellow]")
            for c in r["details"].get("claims", []):
                flag = " (check)" if c.get("needs_review") else ""
                console.print(f"  - {c['label']}{flag}: {c['claim']}" + (f'  [dim]"{c["quote"]}"[/dim]' if c.get("quote") else ""))
