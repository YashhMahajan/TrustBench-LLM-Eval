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
