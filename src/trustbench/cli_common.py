import typer
from rich.console import Console

from .config import database_url
from .db import init_db, make_engine

console = Console()


def get_factory():
    try:
        return init_db(make_engine(database_url()))
    except Exception as e:
        console.print(f"[red]Cannot reach the database:[/red] {e}\n"
                      "Start it with 'docker compose up -d', or set TRUSTBENCH_DATABASE_URL.")
        raise typer.Exit(1)
