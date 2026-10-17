import typer
from rich.console import Console

from .config import database_url

console = Console()


def get_factory():
    from .migrate import MigrationError
    from .db import make_engine
    from .migrate import open_db
    try:
        return open_db(make_engine(database_url()))
    except MigrationError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Cannot reach the database:[/red] {e}\n"
                      "Start it with 'docker compose up -d', or set TRUSTBENCH_DATABASE_URL.")
        raise typer.Exit(1)
