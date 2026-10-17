"""Commands for running the server and managing workspaces and keys."""

import typer
from rich.table import Table

from . import crypto, tenancy
from .cli import app
from .cli_common import console, get_factory
from .dataprotect import PolicyProblem, policy_of, update_policy

ws_app = typer.Typer(help="Workspaces: one for each team or customer.", no_args_is_help=True)
key_app = typer.Typer(help="API keys for the server.", no_args_is_help=True)
secrets_app = typer.Typer(help="Encryption keys.", no_args_is_help=True)
app.add_typer(ws_app, name="workspace")
app.add_typer(key_app, name="key")
app.add_typer(secrets_app, name="secrets")


def _fail(e) -> None:
    console.print(f"[red]{e}[/red]")
    raise typer.Exit(1)


@ws_app.command("create")
def workspace_create(name: str):
    try:
        ws = tenancy.create_workspace(get_factory(), name)
    except tenancy.TenancyError as e:
        _fail(e)
    console.print(f"Workspace '{ws.name}' made (id {ws.id}). It stores no trace content until you turn that on.")
    console.print(f"Next: trustbench key create --workspace {ws.name} --role write")


@ws_app.command("list")
def workspace_list():
    t = Table()
    for col in ["Name", "Id", "Stores content", "Redacts", "Retention", "Sample rate"]:
        t.add_column(col)
    for ws in tenancy.list_workspaces(get_factory()):
        p = policy_of(ws)
        t.add_row(ws.name, ws.id, "yes" if p.store_content else "no", "yes" if p.redact else "[yellow]no[/yellow]", f"{p.retention_days} days", f"{p.sample_rate:.0%}")
    console.print(t)


@ws_app.command("set")
def workspace_set(
    name: str,
    store_content: bool = typer.Option(None, "--store-content/--no-store-content", help="Keep the text of inputs and outputs (redacted and encrypted)"),
    redact: bool = typer.Option(None, "--redact/--no-redact", help="Remove personal data and secrets before storing"),
    retention_days: int = typer.Option(None, help="Delete traces after this many days"),
    sample_rate: float = typer.Option(None, help="Share of traces for online evaluation, 0 to 1"),
):
    """Change the data policy of a workspace. Only the options you give are changed."""
    factory = get_factory()
    changes = {k: v for k, v in dict(store_content=store_content, redact=redact, retention_days=retention_days, sample_rate=sample_rate).items() if v is not None}
    try:
        ws = tenancy.get_workspace(factory, name)
        p = update_policy(factory, ws.id, **changes) if changes else policy_of(ws)
    except (tenancy.TenancyError, PolicyProblem) as e:
        _fail(e)
    console.print_json(p.model_dump_json())
    if not p.redact and p.store_content:
        console.print("[yellow]Content is stored without redaction. Personal data and secrets in it are kept (encrypted).[/yellow]")


@key_app.command("create")
def key_create(workspace: str = typer.Option(..., help="Workspace name"), name: str = typer.Option("key", help="A label"),
               role: str = typer.Option("write", help="read, write or admin")):
    """Make an API key. It is shown once. Copy it now."""
    factory = get_factory()
    try:
        ws = tenancy.get_workspace(factory, workspace)
        row, token = tenancy.create_api_key(factory, ws.id, name, role)
    except tenancy.TenancyError as e:
        _fail(e)
    console.print(f"Key {row.id} ({role}) for workspace '{ws.name}':\n\n  {token}\n")
    console.print("[yellow]This is the only time you can see the key. We store only a hash.[/yellow]")


@key_app.command("list")
def key_list(workspace: str = typer.Option(None)):
    factory = get_factory()
    try:
        ws_id = tenancy.get_workspace(factory, workspace).id if workspace else None
    except tenancy.TenancyError as e:
        _fail(e)
    t = Table()
    for col in ["Id", "Workspace", "Label", "Role", "Starts with", "Last used", "State"]:
        t.add_column(col)
    for k in tenancy.list_api_keys(factory, ws_id):
        t.add_row(str(k.id), k.workspace_id, k.name, k.role, k.prefix, k.last_used_at.strftime("%Y-%m-%d %H:%M") if k.last_used_at else "never",
                  "revoked" if k.revoked_at else "active")
    console.print(t)


@key_app.command("revoke")
def key_revoke(key_id: int):
    try:
        tenancy.revoke_api_key(get_factory(), key_id)
    except tenancy.TenancyError as e:
        _fail(e)
    console.print(f"Key {key_id} is revoked.")


@secrets_app.command("generate-key")
def secrets_generate():
    """Make an encryption key for stored content. Keep it safe: without it the content cannot be read."""
    console.print(crypto.generate_key())
    console.print("[dim]Set it as TRUSTBENCH_SECRET_KEY. To rotate, put the new key first: NEW,OLD[/dim]")


@app.command()
def serve(host: str = typer.Option("127.0.0.1", help="Use 0.0.0.0 only behind a proxy with TLS"), port: int = typer.Option(8000)):
    """Start the HTTP API (traces in, results out). Docs at /docs."""
    import uvicorn

    from .api import create_app

    factory = get_factory()
    tenancy.ensure_default_workspace(factory)
    if host not in ("127.0.0.1", "localhost", "::1"):
        console.print("[yellow]The server listens on a public address. API keys travel in headers, so put TLS in front of it.[/yellow]")
    uvicorn.run(create_app(factory), host=host, port=port, log_level="info")


jobs_app = typer.Typer(help="The task queue.", no_args_is_help=True)
app.add_typer(jobs_app, name="jobs")


@app.command()
def worker(
    concurrency: int = typer.Option(2, help="Tasks to run at the same time"),
    purge_every: int = typer.Option(3600, help="Seconds between deletions of expired traces. 0 turns it off."),
):
    """Run background tasks: online evaluation of traces and deletion of old traces.

    Start as many workers as you need, on any machine that reaches the database (or Redis).
    """
    import logging
    import threading

    from .handlers import default_handlers
    from .jobs import install_signal_handlers, make_queue, run_worker
    from .traces import purge_expired

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    factory = get_factory()
    stop = threading.Event()
    install_signal_handlers(stop)
    periodic = [(float(purge_every), lambda: logging.getLogger("trustbench.worker").info("purged %d expired trace(s)", purge_expired(factory)))] if purge_every else []
    console.print(f"Worker started with {concurrency} thread(s). Press Ctrl+C to stop.")
    handled = run_worker(make_queue(factory), default_handlers(factory), concurrency=concurrency, stop=stop, periodic=periodic)
    console.print(f"Stopped. {handled} task(s) handled.")


@jobs_app.command("status")
def jobs_status():
    from .jobs import make_queue
    c = make_queue(get_factory()).counts()
    console.print(f"queued {c['queued']}, running {c['running']}, done {c['done']}, dead {c['dead']}")


@jobs_app.command("dead")
def jobs_dead(limit: int = 20):
    """Tasks that failed too often. Look at the error, fix the cause, then retry."""
    from .jobs import make_queue
    t = Table(title="Dead tasks")
    for col in ["Id", "Kind", "Attempts", "Workspace", "Last error"]:
        t.add_column(col)
    for d in make_queue(get_factory()).dead(limit):
        t.add_row(d["id"], d["kind"] or "-", str(d["attempts"]), d["workspace_id"] or "-", (d["error"] or "")[:90])
    console.print(t)


@jobs_app.command("retry")
def jobs_retry(job_id: str):
    from .jobs import make_queue
    if make_queue(get_factory()).retry_dead(job_id):
        console.print(f"Task {job_id} is queued again.")
    else:
        _fail(f"Task {job_id} is not a dead task.")


@app.command()
def drift(
    workspace: str = typer.Argument("default", help="Workspace name"),
    current_days: float = typer.Option(7, help="The recent window, in days"),
    reference_days: float = typer.Option(30, help="The window before it, in days"),
    name: str = typer.Option(None, help="Only traces of this feature"),
    all_features: bool = typer.Option(False, "--all", help="Also show the features that did not change"),
):
    """Has the production traffic or behaviour changed? Compares the recent window with the one before."""
    from .drift import drift_report

    factory = get_factory()
    try:
        ws = tenancy.get_workspace(factory, workspace)
    except tenancy.TenancyError as e:
        _fail(e)
    rep = drift_report(factory, ws.id, current_days, reference_days, name)
    console.print(f"Reference window: {rep.reference_traces} traces. Current window: {rep.current_traces} traces.")
    for w in rep.warnings:
        console.print(f"[yellow]{w}[/yellow]")
    shown = rep.findings if all_features else rep.drifted
    if rep.findings and not shown:
        console.print(f"[green]No feature drifted[/green] ({len(rep.findings)} compared).")
    if shown:
        t = Table(title="Changes between the windows")
        for col in ["Feature", "Change", "PSI / difference", "p-value", "Before", "Now"]:
            t.add_column(col)
        for f in shown:
            colour = {"large": "red", "moderate": "yellow", "none": "dim"}[f.level]
            stat = "-" if f.statistic is None else (f"{f.statistic:+.1%}" if f.kind == "rate" else f"{f.statistic:.2f}")
            t.add_row(f.feature, f"[{colour}]{f.level if f.drifted else 'none'}[/{colour}]", stat, "-" if f.p_value is None else f"{f.p_value:.2g}", f.reference, f.current)
        console.print(t)
        console.print("[dim]A change is a signal to look, not proof that quality got worse. New kinds of users can change the traffic.[/dim]")


traces_app = typer.Typer(help="Production traces.", no_args_is_help=True)
app.add_typer(traces_app, name="traces")


def _workspace(factory, name):
    try:
        return tenancy.get_workspace(factory, name)
    except tenancy.TenancyError as e:
        _fail(e)


@traces_app.command("list")
def traces_list(workspace: str = typer.Argument("default"), status: str = typer.Option(None), name: str = typer.Option(None), limit: int = 20):
    from .traces import list_traces
    factory = get_factory()
    ws = _workspace(factory, workspace)
    t = Table()
    for col in ["Trace", "Name", "When (UTC)", "Status", "Model", "Latency", "Cost", "Feedback", "Check"]:
        t.add_column(col)
    for r in list_traces(factory, ws.id, status=status, name=name, limit=limit):
        t.add_row(r.trace_id[:16], r.name, r.started_at.strftime("%m-%d %H:%M"), "[red]error[/red]" if r.status == "error" else "ok", r.model or "-",
                  f"{r.duration_ms:.0f} ms", "-" if r.cost_usd is None else f"${r.cost_usd:.4f}", "-" if r.feedback is None else f"{r.feedback:+d}", r.eval_state)
    console.print(t)


@traces_app.command("show")
def traces_show(trace_id: str, workspace: str = typer.Option("default"), reveal: bool = typer.Option(False, "--reveal", help="Print the stored text (needs the encryption key)")):
    from . import audit
    from .online import trace_evaluations
    from .traces import get_trace, reveal as reveal_text
    factory = get_factory()
    ws = _workspace(factory, workspace)
    found = get_trace(factory, ws.id, trace_id)
    if not found:
        _fail("No such trace.")
    row, spans = found
    console.print(f"[bold]{row.trace_id}[/bold] {row.name}  {row.status}  {row.duration_ms:.0f} ms  model {row.model or '-'}  feedback {row.feedback}")
    console.print(f"Input {row.input_chars} chars, output {row.output_chars} chars, removed from text: {row.redactions or 'nothing'}, expires {row.expires_at:%Y-%m-%d}")
    for sp in spans:
        console.print(f"  {'  ' if sp.parent_id else ''}- {sp.kind}:{sp.name} +{sp.offset_ms:.0f} ms for {sp.duration_ms:.0f} ms {sp.status} {sp.attrs or ''}")
    for e in trace_evaluations(factory, ws.id, row.id):
        console.print(f"  check {e.evaluator} ({e.kind}): {'pass' if e.passed else 'FAIL'} - {e.rationale}")
    if reveal:
        try:
            for k, v in reveal_text(row).items():
                console.print(f"[bold]{k}:[/bold] {v}")
        except (crypto.SecretKeyMissing, crypto.DecryptionError) as e:
            _fail(e)


@traces_app.command("flagged")
def traces_flagged(workspace: str = typer.Argument("default"), name: str = typer.Option(None)):
    """Traces that went wrong: an error, a bad rating or a failed online check."""
    from .production_tests import flagged_traces
    factory = get_factory()
    ws = _workspace(factory, workspace)
    t = Table(title="Flagged traces")
    for col in ["Trace", "Feature", "Why", "Text stored"]:
        t.add_column(col)
    for f in flagged_traces(factory, ws.id, name):
        t.add_row(f.trace_id, f.name, ", ".join(f.reasons), "yes" if f.has_content else "no")
    console.print(t)
    console.print("Turn one into a test: trustbench traces promote TRACE_ID")


@traces_app.command("promote")
def traces_promote(trace_id: str, workspace: str = typer.Option("default"), answer: str = typer.Option(None, help="The right answer, if you know it"),
                   dataset: str = typer.Option(None, help="Dataset name. Default: FEATURE-production")):
    """Propose a regression test from a production trace. A person must approve it in 'trustbench proposals'."""
    from .production_tests import PromoteError, promote
    factory = get_factory()
    ws = _workspace(factory, workspace)
    try:
        p = promote(factory, ws.id, trace_id, answer, dataset)
    except PromoteError as e:
        _fail(e)
    console.print(f"Proposed test {p.id} in dataset '{p.dataset_name}' ({'answer checked by rule or by you' if p.expected_verified else 'a judge model decided this is a failure: check it'}).")
    console.print(f"Review: trustbench proposals show {p.id}   Approve: trustbench proposals approve {p.id}" + ("" if p.expected_verified else " --checked-answers"))


@traces_app.command("purge")
def traces_purge():
    """Delete all traces that are past their retention time."""
    from .traces import purge_expired
    console.print(f"Deleted {purge_expired(get_factory())} expired trace(s).")


@app.command()
def monitor(workspace: str = typer.Argument("default"), days: float = typer.Option(7), name: str = typer.Option(None, help="Only traces of this feature"),
            as_json: bool = typer.Option(False, "--json")):
    """The state of a production system: errors, latency, cost, online checks. Each number says how it was obtained."""
    import json

    from .monitor import monitor_summary

    factory = get_factory()
    ws = _workspace(factory, workspace)
    d = monitor_summary(factory, ws.id, days, name)
    if as_json:
        typer.echo(json.dumps(d, indent=2, default=str))
        return
    console.print(f"Last {days:g} day(s): {d['traces']} traces" + (f" of '{name}'" if name else ""))
    if not d["traces"]:
        return
    m, pct = d["measured"], (lambda r: "-" if r["value"] is None else f"{r['value']:.1%} ({r['ci'][0]:.1%} to {r['ci'][1]:.1%}), n={r['n']}")
    t = Table(title="Measured: counted or timed")
    t.add_column("Measure")
    t.add_column("Value")
    t.add_row("Error rate", pct(m["error_rate"]))
    lat = m["latency_ms"]
    t.add_row("Latency P50 / P95 / P99", f"{lat['p50']:.0f} / {lat['p95']:.0f} / {lat['p99']:.0f} ms")
    c = m["cost_usd"]
    t.add_row("Cost", "unknown (no prices)" if c["total"] is None else f"${c['total']:.4f} (${c['per_trace']:.5f} per trace, {c['priced_share']:.0%} of traces priced)")
    t.add_row("Empty answers", pct(m["empty_answer_rate"]))
    fb = m["user_feedback"]
    t.add_row("Bad user feedback", pct(fb["bad"]) + f", {fb['rated_share']:.0%} of traces rated")
    for k, v in m.items():
        if k.startswith("check:"):
            t.add_row(f"Rule check {k[6:]}", f"{v['pass_rate']['value']:.1%} pass, on {v['coverage']:.0%} of traces")
    console.print(t)
    if d["estimated"]:
        e = Table(title="Estimated: a judge model's opinion on a sample. A judge can be wrong.")
        e.add_column("Check")
        e.add_column("Pass rate")
        for k, v in d["estimated"].items():
            e.add_row(k[6:], f"{pct(v['pass_rate'])}, on {v['coverage']:.0%} of traces")
        console.print(e)
    o = d["online_checks"]
    console.print(f"Online checks: {o['sampled_share']:.0%} sampled, {o['evaluated']} done, {o['pending']} waiting, {o['failed_to_evaluate']} failed.")
    console.print("[bold]Not measured:[/bold]")
    for u in d["unmeasured"]:
        console.print(f"  - {u}")
    console.print("[dim]Drift: trustbench drift. Failures to tests: trustbench traces flagged[/dim]")
