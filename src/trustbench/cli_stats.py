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


@app.command()
def report(
    run_id: str,
    model: str = typer.Option(None, help="Show one model only"),
    as_json: bool = typer.Option(False, "--json", help="Print JSON instead of tables"),
):
    """Full report of a run: pass rates with intervals, weak slices, latency, cost and more."""
    import json

    from .summary import run_summary

    try:
        rd = load_run(get_factory(), run_id)
        data = run_summary(rd)
        if model:
            rd.pick_model(model)
            data["models"] = {model: data["models"][model]}
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    pct = lambda x: "-" if x is None else f"{x:.1%}"
    console.print(f"Run {data['run_id']}  dataset {data['dataset']} ({data['dataset_version']})  status: {data['status']}")
    for name, m in data["models"].items():
        t = Table(title=name)
        for col in ["Group", "Cases", "Pass rate", "95% interval"]:
            t.add_column(col)
        for label, s in [("overall", m["overall"]), *m["by_task"].items()]:
            t.add_row(label, str(s["n"]), pct(s["rate"]), "-" if s["ci"] is None else f"{s['ci'][0]:.1%} to {s['ci'][1]:.1%}")
        console.print(t)
        o = m["operations"]
        ms = lambda v: "-" if v is None else f"{v / 1000:.2f} s"
        console.print(f"Latency P50 {ms(o['latency_ms'][50])}, P95 {ms(o['latency_ms'][95])}, P99 {ms(o['latency_ms'][99])}. "
                      f"Call failures {pct(o['call_failure_rate'])}. Retries {pct(o['retry_rate'])}. "
                      f"Cost per success {'unknown' if o['cost_per_success_usd'] is None else '$%.4f' % o['cost_per_success_usd']}.")
        left = m["left_out"]
        if left["skipped_cases"] or left["evaluator_errors"]:
            console.print(f"[yellow]Left out of the rates: {left['skipped_cases']} skipped case(s), "
                          f"{left['evaluator_errors']} evaluator problem(s).[/yellow]")
        if m["weak_slices"]:
            console.print("[bold]Weak slices[/bold] (clearly lower than the other cases):")
            for s in m["weak_slices"]:
                console.print(f"  {s['dimension']} = {s['value']}: {pct(s['rate'])} against {pct(s['rest_rate'])} (n={s['n']})")
        if "hallucination" in m:
            h = m["hallucination"]
            console.print(f"Claims: {h['total']} checked. Supported {pct(h['ec'])}, unsupported {pct(h['ucr'])}, "
                          f"contradicted {pct(h['cr'])}, could not judge {pct(h['unassessed_rate'])}.")
        if m["needs_human_review"]:
            console.print(f"[yellow]{m['needs_human_review']} result(s) need a person to check them. Use 'trustbench show'.[/yellow]")
        if "calibration" in m:
            console.print(f"Calibration: run 'trustbench calibration {data['run_id']}' for details.")


robust_app = typer.Typer(help="Test if the model stays stable when the input changes.", no_args_is_help=True)
app.add_typer(robust_app, name="robustness")


@robust_app.command("build")
def robustness_build(
    dataset: str = typer.Argument(..., help="Built-in suite name or dataset file"),
    transforms: str = typer.Option("whitespace,formatting,typos,reorder_context,evidence_first,evidence_last,evidence_buried",
                                   help="Comma-separated list. See 'trustbench robustness list'."),
    paraphrase_judge: str = typer.Option(None, "--paraphrase-judge", help="provider:model that writes paraphrases"),
    seed: int = typer.Option(0),
    out: Path = typer.Option(None, help="File to write. Default: NAME_robust.jsonl"),
):
    """Write a new dataset file: the original cases plus changed copies that must give the same result."""
    import asyncio
    import json

    from .datasets import DatasetError, load_cases, resolve_path
    from .engine import make_judge
    from .paraphrase import build_paraphrases
    from .providers import get_provider
    from .transforms import build_variants

    try:
        path = resolve_path(dataset)
        cases = [c for c in load_cases(path) if not c.metadata.get("source_case")]
        names = [n.strip() for n in transforms.split(",") if n.strip()]
        variants, rep = build_variants(cases, names, seed)
        para = None
        if paraphrase_judge:
            para, prep = asyncio.run(build_paraphrases(make_judge(paraphrase_judge, get_provider), cases))
            variants += para
    except (DatasetError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    out = out or Path(f"{path.stem}_robust.jsonl")
    with out.open("w") as f:
        for c in [*cases, *variants]:
            f.write(json.dumps(c.model_dump(), ensure_ascii=False) + "\n")
    t = Table(title=f"{len(cases)} original cases, {len(variants)} variants")
    for col in ["Transform", "Made", "Not applicable", "Dropped (unfair)"]:
        t.add_column(col)
    for n in names:
        t.add_row(n, str(rep.applied[n]), str(rep.not_applicable[n]), str(rep.rejected[n]))
    if para is not None:
        t.add_row("paraphrase", str(prep.applied), "-", str(sum(prep.rejected.values())))
    console.print(t)
    console.print(f"Wrote {out}. Run it with: trustbench run --dataset {out}")


@robust_app.command("list")
def robustness_list():
    """Show the available transforms."""
    from .transforms import TRANSFORMS
    for t in TRANSFORMS.values():
        console.print(f"[bold]{t.name}[/bold]  {t.description}")
    console.print("[bold]paraphrase[/bold]  A rewrite by a model. Use --paraphrase-judge.")


@robust_app.command("report")
def robustness_report_cmd(run_id: str, model: str = typer.Option(None)):
    """Compare the results on original cases and on their changed copies."""
    from .robustness import robustness_report

    try:
        r = robustness_report(load_run(get_factory(), run_id).for_model(model))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if r.pairs == 0:
        console.print(f"[red]{r.warnings[0]}[/red]")
        raise typer.Exit(1)

    def rate(x):
        return "-" if x.value is None else f"{x.value:.1%} ({x.ci[0]:.1%} to {x.ci[1]:.1%}), {x.k} of {x.n}"

    t = Table(title=f"Robustness, {r.pairs} pairs of original and changed case")
    t.add_column("Measure")
    t.add_column("Value")
    t.add_row("Pass rate: originals", f"{r.original_rate:.1%}")
    t.add_row("Pass rate: changed copies", f"{r.variant_rate:.1%}")
    t.add_row("Degradation (original minus copy)", f"{r.degradation:+.1%} (95% interval {r.degradation_ci[0]:+.1%} to {r.degradation_ci[1]:+.1%})")
    t.add_row("Break rate (passed original, failed copy)", rate(r.break_rate))
    t.add_row("Fix rate (failed original, passed copy)", rate(r.fix_rate))
    t.add_row("Consistency (same result for both)", rate(r.consistency))
    t.add_row("Consistently wrong (failed both)", rate(r.consistently_wrong))
    console.print(t)
    g = Table(title="By transform")
    for col in ["Transform", "Pairs", "Original", "Copy", "Break rate"]:
        g.add_column(col)
    for x in r.by_transform:
        g.add_row(x.name, str(x.pairs), f"{x.original_rate:.0%}", f"{x.variant_rate:.0%}",
                  "-" if x.break_rate.value is None else f"{x.break_rate.value:.0%} ({x.break_rate.k}/{x.break_rate.n})")
    console.print(g)
    if r.worst_transform:
        console.print(f"Weakest transform: [bold]{r.worst_transform.name}[/bold] ({r.worst_transform.variant_rate:.0%} pass).")
    if r.worst_task:
        console.print(f"Weakest task: [bold]{r.worst_task.name}[/bold] ({r.worst_task.variant_rate:.0%} pass).")
    if r.fragile_cases:
        console.print("Cases that broke: " + "; ".join(f"{c} ({', '.join(ts)})" for c, ts in list(r.fragile_cases.items())[:10]))
    for w in r.warnings:
        console.print(f"[yellow]{w}[/yellow]")


prop_app = typer.Typer(help="New test cases proposed from failures. A person reviews them.", no_args_is_help=True)
app.add_typer(prop_app, name="proposals")


@prop_app.command("generate")
def proposals_generate(
    run_id: str,
    model: str = typer.Option(None, help="Model whose failures to use, if the run has several"),
    judge: str = typer.Option(None, help="provider:model that also writes new questions. Their answers are not verified."),
    per_case: int = typer.Option(2, help="Proposals for each failed case"),
):
    """Propose new test cases based on the failed cases of a run."""
    import asyncio

    from .adversary import propose_from_failures
    from .engine import make_judge
    from .providers import get_provider

    try:
        rep = asyncio.run(propose_from_failures(get_factory(), run_id, model, make_judge(judge, get_provider), per_case))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"{rep.failing_cases} failed case(s). {rep.created} proposal(s) created.")
    for reason, n in sorted(rep.skipped.items()):
        console.print(f"  skipped {n}: {reason}")
    if rep.created:
        console.print("Review them with: trustbench proposals list")


@prop_app.command("list")
def proposals_list(status: str = typer.Option("proposed", help="proposed, approved, rejected or all"),
                   dataset: str = typer.Option(None)):
    from . import proposals
    from .datasets import render_prompt

    rows = proposals.list_proposals(get_factory(), None if status == "all" else status, dataset)
    t = Table(title=f"{len(rows)} proposal(s)")
    for col in ["Id", "Status", "From", "Method", "Answer checked", "Input"]:
        t.add_column(col)
    for p in rows:
        text = render_prompt(p.spec["input"]).replace("\n", " ")
        t.add_row(str(p.id), p.status, p.source_case_id or "-", p.method, "yes" if p.expected_verified else "[yellow]no[/yellow]",
                  text[:70] + ("..." if len(text) > 70 else ""))
    console.print(t)


@prop_app.command("show")
def proposals_show(proposal_id: int):
    from . import proposals

    p = next((x for x in proposals.list_proposals(get_factory()) if x.id == proposal_id), None)
    if p is None:
        console.print(f"[red]No proposal {proposal_id}.[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{p.case_id}[/bold]  {p.status}  from {p.source_case_id}  by {p.method}")
    console.print(f"Input: {p.spec['input']}")
    console.print(f"Expected: {p.spec['expected']}  ({'checked' if p.expected_verified else 'NOT checked: a model wrote it'})")
    console.print(f"Evaluators: {p.spec['evaluation']}")


@prop_app.command("approve")
def proposals_approve(ids: list[int], checked_answers: bool = typer.Option(False, "--checked-answers",
                      help="I checked the answers that a model wrote"), note: str = typer.Option("")):
    from . import proposals

    try:
        n = proposals.approve(get_factory(), ids, checked_answers, note)
    except proposals.ReviewError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Approved {n}.")


@prop_app.command("reject")
def proposals_reject(ids: list[int], note: str = typer.Option("")):
    from . import proposals

    try:
        n = proposals.reject(get_factory(), ids, note)
    except proposals.ReviewError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Rejected {n}.")


@prop_app.command("edit")
def proposals_edit(proposal_id: int, answer: str = typer.Option(..., help="The right answer")):
    """Correct the answer of a proposal. Then it counts as checked."""
    from . import proposals

    try:
        proposals.edit_answer(get_factory(), proposal_id, answer)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print("Updated. The answer now counts as checked.")


@prop_app.command("export")
def proposals_export(dataset: str, out: Path = typer.Option(None, help="File to write. Default: DATASET_regression.jsonl"),
                     with_base: bool = typer.Option(True, "--with-base/--only-new", help="Include the cases of the original dataset")):
    """Write the approved cases as a dataset file. Run it to check that old failures stay fixed."""
    from . import proposals
    from .datasets import DatasetError, load_cases, resolve_path

    base = None
    if with_base:
        try:
            base = load_cases(resolve_path(dataset))
        except DatasetError:
            console.print(f"[yellow]The original dataset '{dataset}' was not found. Writing the approved cases only.[/yellow]")
    out = out or Path(f"{dataset}_regression.jsonl")
    try:
        n, version = proposals.export_approved(get_factory(), dataset, out, base)
    except (proposals.ReviewError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print(f"Wrote {out} with {n} approved case(s) (dataset version {version}). Run it with: trustbench run --dataset {out}")


@app.command()
def failures(
    run_id: str,
    model: str = typer.Option(None, help="Model to analyze, if the run has several"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Why did cases fail? Labels, recurring patterns, retrieval stage and what to fix first."""
    import json

    from .failure_report import analyze_failures

    try:
        data = analyze_failures(load_run(get_factory(), run_id).for_model(model))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    if not data["failed_cases"]:
        console.print(f"No case failed ({data['cases_with_a_result']} cases with a result).")
        return
    console.print(f"{data['failed_cases']} of {data['cases_with_a_result']} cases failed.")
    t = Table(title="How the cases failed (a case can have several labels)")
    for col in ["Label", "Cases", "Share", "Meaning"]:
        t.add_column(col)
    for row in data["labels"]:
        t.add_row(row["label"], str(row["cases"]), f"{row['share']:.0%}", row["meaning"])
    console.print(t)
    if data["clusters"]:
        c = Table(title="Recurring patterns")
        for col in ["#", "Cases", "Share", "Words", "Tasks", "Cohesion", "Examples"]:
            c.add_column(col)
        for x in data["clusters"]:
            c.add_row(str(x["id"]), str(x["size"]), f"{x['share']:.0%}", ", ".join(x["terms"]) or "-",
                      ", ".join(x["tasks"]), f"{x['cohesion']:.2f}", ", ".join(x["examples"]))
        console.print(c)
        console.print("[dim]Patterns show what failures have in common. They do not prove the cause.[/dim]")
    if data["rag_stages"]:
        console.print("Retrieval stage of the failures: " + ", ".join(f"{k} {v}" for k, v in data["rag_stages"].items()))
    p = Table(title="What to fix first")
    for col in ["Failure type", "Cases", "Severity", "Frequency", "Exposure", "Detectability", "Score"]:
        p.add_column(col)
    for i in data["priority"]:
        p.add_row(i["name"], str(i["cases"]), f"{i['severity']} (x{i['weight']})", f"{i['frequency_pct']:.1f}%",
                  f"{i['exposure']:.1f}", f"{i['detectability']:.1f}", f"{i['score']:.1f}")
    console.print(p)
    console.print("[dim]Score = severity weight x frequency (%) x exposure x detectability.[/dim]")
    if data["needs_review"]:
        console.print(f"[yellow]{len(data['needs_review'])} failure(s) need a person to check them: {', '.join(data['needs_review'][:8])}[/yellow]")
