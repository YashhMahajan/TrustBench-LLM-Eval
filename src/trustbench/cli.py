from contextlib import contextmanager
from pathlib import Path

import typer
from rich.table import Table

from .cli_common import console, get_factory
from .config import detect_models

app = typer.Typer(help="TrustBench: evaluate LLMs. Set an API key and run.", no_args_is_help=True)
_factory = get_factory


def _show(factory, run_id: str, batch: int | None = None) -> None:
    from . import report
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
    dataset: str = typer.Option(None, help="Built-in suite name or path to .jsonl/.json/.csv (default: the config file, else smoke)"),
    model: list[str] = typer.Option(None, "--model", "-m", help="provider:model, python:module:function@label or http:URL@label. Repeat for more."),
    target: list[str] = typer.Option(None, "--target", "-t", help="A target name from trustbench.yaml. Repeat for more."),
    config: Path = typer.Option(None, "--config", help="Config file (default: ./trustbench.yaml when it exists)"),
    reps: int = typer.Option(None, help="Repetitions for each case (default 1)"),
    concurrency: int = typer.Option(None, help="Calls at the same time (default 4)"),
    retries: int = typer.Option(None, help="Retries of a failed call (default 2)"),
    timeout: float = typer.Option(None, help="Seconds for each model call (default 60)"),
    budget: float = typer.Option(None, help="Stop starting new calls after this many USD"),
    name: str = typer.Option(None, help="A label for the run"),
    judge: str = typer.Option(None, help="provider:model that grades open answers"),
    rpm: float = typer.Option(None, help="Most calls to start in one minute for each provider"),
    system_prompt_file: Path = typer.Option(None, "--system-prompt-file", help="A file with the system prompt. Its text is stored with the run."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ask the model again, even when the answer is stored"),
):
    """Run a dataset against your models or apps. Reads trustbench.yaml when it exists. Flags win over the file."""
    from .datasets import DatasetError
    from .engine import RunOptions
    from .datasets import import_dataset
    from .datasets import load_cases
    from . import quality
    from .datasets import resolve_path
    from .engine import RunInterrupted, run_eval
    from .providers import allow_app_targets
    allow_app_targets()                      # Only the CLI and pytest may run python: and http: targets.
    from .edd.config import ConfigError, RunSettings, load_config
    try:
        cfg = load_config(config)
        if target and not cfg:
            raise ConfigError("--target needs a trustbench.yaml. Create one with 'trustbench init'.")
        specs = model or (cfg.specs(target) if cfg and (cfg.targets or target) else None) or detect_models()
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    settings = cfg.run if cfg else RunSettings()
    dataset = dataset or (cfg.dataset if cfg else None) or "smoke"
    judge = judge or (cfg.judge if cfg else None)
    reps, concurrency = reps or settings.reps, concurrency or settings.concurrency
    retries = settings.retries if retries is None else retries
    timeout, budget, rpm = timeout or settings.timeout, budget or settings.budget, rpm or settings.rpm
    prompt_path = system_prompt_file or (Path(settings.system_prompt_file) if settings.system_prompt_file else None)
    if prompt_path is not None and not prompt_path.is_file():
        console.print(f"[red]System prompt file not found: {prompt_path}[/red]")
        raise typer.Exit(1)
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
    from .tenancy import ensure_default_workspace
    workspace_id = ensure_default_workspace(factory).id
    with factory() as s:
        version = import_dataset(s, path.stem, cases, workspace_id)
    console.print(f"Dataset {version.name} version {version.content_hash}: {version.case_count} cases. "
                  f"Models: {', '.join(specs)}")
    try:
        with _progress_bar() as update:
            run_id = run_eval(factory, version.id, specs,
                              RunOptions(repetitions=reps, concurrency=concurrency, retries=retries,
                                         timeout=timeout, budget_usd=budget, name=name, judge=judge,
                                         rpm=rpm, cache=settings.cache and not no_cache,
                                         system_prompt=prompt_path.read_text() if prompt_path else None),
                              workspace_id=workspace_id, progress=update)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except RunInterrupted as e:
        _stopped(e)
    _show(factory, run_id)


def _stopped(e) -> None:
    console.print(f"[yellow]Stopped. Saved results are kept. Finish the run with:[/yellow] trustbench resume {e.run_id}")
    raise typer.Exit(130)


@app.command()
def resume(run_id: str, budget: float = typer.Option(None, help="A new budget in USD for the part that is left")):
    """Finish a run that stopped (Ctrl-C, a crash or a budget). Finished cases are not run again."""
    from .engine import RunInterrupted, resume_run
    from .providers import allow_app_targets
    allow_app_targets()
    factory = _factory()
    try:
        with _progress_bar() as update:
            resume_run(factory, run_id, progress=update, budget_usd=budget)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except RunInterrupted as e:
        _stopped(e)
    _show(factory, run_id)


@contextmanager
def _progress_bar():
    """A progress bar that shows nothing when the output is not a terminal."""
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
    bar = Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(),
                   console=console, transient=True, disable=not console.is_terminal)
    task = bar.add_task("Running", total=None)

    def update(p) -> None:
        bar.update(task, total=p.total, completed=p.done,
                   description=f"Running ({p.failed} failed, {p.cached} from cache, ${p.cost_usd:.4f})")

    with bar:
        yield update


cache_app = typer.Typer(help="Stored model answers. Asking the same thing again costs nothing.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")


@cache_app.command("stats")
def cache_stats():
    """Show how many answers are stored."""
    from sqlalchemy import func, select
    from .db import ResponseCache
    with _factory()() as s:
        n, size, oldest = s.execute(select(func.count(), func.coalesce(func.sum(func.length(ResponseCache.text)), 0),
                                           func.min(ResponseCache.created_at))).one()
    console.print(f"{n} stored answers, {size / 1024:.1f} KB of text." + (f" The oldest is from {oldest:%Y-%m-%d}." if oldest else ""))


@cache_app.command("clear")
def cache_clear(model: str = typer.Option(None, help="Only this provider:model"),
                yes: bool = typer.Option(False, "--yes", help="Do not ask")):
    """Delete stored answers. Runs keep their results. Only new requests are affected."""
    from sqlalchemy import delete
    from .db import ResponseCache
    from .providers import split_spec
    if not yes and not typer.confirm("Delete the stored answers?"):
        raise typer.Exit(1)
    stmt = delete(ResponseCache)
    if model:
        provider, name = split_spec(model)
        stmt = stmt.where(ResponseCache.provider == provider, ResponseCache.model == name)
    with _factory().begin() as s:
        n = s.execute(stmt).rowcount
    console.print(f"Deleted {n} stored answers.")


@app.command("rescore")
def rescore_cmd(run_id: str, judge: str = typer.Option(None, help="provider:model that grades open answers")):
    """Score the stored outputs of a run again. No model calls."""
    from .engine import rescore
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
    from . import report
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
    from .datasets import DatasetError
    from .datasets import load_cases
    from . import quality
    from .datasets import resolve_path
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
    from . import annotations
    count = annotations.export_template(_factory(), run_id, out)
    console.print(f"Wrote {count} answers to {out}. Fill in the 'label' column.")


@notes.command("import")
def annotations_import(
    file: Path,
    annotator: str = typer.Option(..., help="Who labelled the answers"),
    dimension: str = typer.Option("correctness", help="What the label measures"),
):
    """Store the labels from a CSV."""
    from . import annotations
    try:
        stored, skipped = annotations.import_annotations(_factory(), file, annotator, dimension)
    except annotations.AnnotationError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Stored {stored} label(s). Skipped {skipped} empty row(s).")


@app.command("validate-judge")
def validate_judge_cmd(run_id: str, dimension: str = typer.Option("correctness", help="Label dimension to compare")):
    """Compare the judge grades of a run with human labels."""
    from .judge_validation import validate_judge
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
    from .datasets import DatasetError
    from .datasets import load_cases
    from .datasets import resolve_path
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
    from . import report
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


@app.command()
def calibration(run_id: str, model: str = typer.Option(None, help="provider:model, if the run has several")):
    """Does the stated confidence match the accuracy? Uses the 'calibration' suite."""
    from .calibration import calibration_from_records
    from .rundata import load_run

    try:
        rd = load_run(_factory(), run_id)
        r = calibration_from_records(rd.for_model(model))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if r.n == 0 and not r.unreadable:
        console.print("[red]This run has no calibrated answers. Run the 'calibration' suite.[/red]")
        raise typer.Exit(1)

    def f(x, pct=False):
        return "-" if x is None else (f"{x:.0%}" if pct else f"{x:.3f}")

    t = Table(title=f"Calibration, {r.n} answers")
    t.add_column("Measure")
    t.add_column("Value")
    t.add_row("Accuracy", f(r.accuracy, True))
    t.add_row("Mean stated confidence", f(r.mean_confidence, True))
    ci = f" (95% CI {r.brier_ci[0]:.3f} to {r.brier_ci[1]:.3f})" if r.brier_ci else ""
    t.add_row("Brier score (lower is better)", f(r.brier) + ci)
    t.add_row("Expected calibration error", f(r.ece))
    t.add_row("AUROC (0.5 = confidence tells nothing)", f(r.auroc))
    t.add_row("Error rate if it answers its top 50%", f(r.risk_at_50, True))
    t.add_row("Error rate if it answers its top 80%", f(r.risk_at_80, True))
    console.print(t)
    for w in r.warnings:
        console.print(f"[yellow]{w}[/yellow]")


from . import cli_stats, cli_server, cli_selftest, cli_db, cli_edd  # noqa: E402,F401  (add compare, gate, baseline, policy, workspace, key, serve and init commands)
