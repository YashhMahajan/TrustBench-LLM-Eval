import json

import typer
from rich.table import Table

from .cli import app
from .cli_common import console
from .selftest import AREAS, run_selftest


@app.command()
def selftest(
    quick: bool = typer.Option(False, "--quick", help="Fewer simulations. About four times faster, a little less sharp."),
    area: list[str] = typer.Option(None, "--area", help=f"Only these areas: {', '.join(AREAS)}"),
    seed: int = typer.Option(0, help="Seed of the simulations. The same seed gives the same numbers."),
    as_json: bool = typer.Option(False, "--json", help="Print the results as JSON"),
):
    """Check that TrustBench measures correctly. Uses data with a known truth. Exit code 1 if a check fails."""
    shown = []

    def live(r):
        if not as_json:
            console.print(f"{'[green]pass[/green]' if r.passed else '[red]FAIL[/red]'}  [dim]{r.area}[/dim]  {r.name}  [dim]({r.seconds:.1f} s)[/dim]")
        shown.append(r)

    results = run_selftest(quick, seed, area or None, live)
    if as_json:
        typer.echo(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        t = Table(title=f"Self-test: {sum(r.passed for r in results)} of {len(results)} checks passed")
        for col in ["", "Area", "Check", "Observed", "Target"]:
            t.add_column(col)
        for r in results:
            t.add_row("[green]pass[/green]" if r.passed else "[red]FAIL[/red]", r.area, r.name, r.observed, r.target)
        console.print(t)
        for r in results:
            if not r.passed:
                console.print(f"[red]{r.area}: {r.name}[/red]\n  {r.detail}")
        console.print("[dim]The targets were set from theory before the numbers were seen. A failure is a finding about TrustBench.[/dim]")
    raise typer.Exit(0 if all(r.passed for r in results) else 1)
