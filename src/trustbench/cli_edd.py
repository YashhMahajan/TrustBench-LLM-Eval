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
