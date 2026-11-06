import typer

from .cli import app
from .cli_common import console
from .config import database_url

db_app = typer.Typer(help="The database schema and its versions.", no_args_is_help=True)
app.add_typer(db_app, name="db")


def _engine():
    from .db import make_engine
    try:
        return make_engine(database_url())
    except Exception as e:
        console.print(f"[red]Cannot open the database:[/red] {e}")
        raise typer.Exit(1)


@db_app.command("upgrade")
def db_upgrade():
    """Bring the database to the newest schema. TrustBench creates a new database. Other commands do this by themselves."""
    from . import migrate
    try:
        what = migrate.ensure_schema(_engine())
    except migrate.MigrationError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print({"created": "The database was created.", "upgraded": "The database was upgraded.", "stamped": "The database matches the schema. It is now under version control.",
                   "current": "The database is already up to date."}[what])


@db_app.command("status")
def db_status():
    """Show the version of the database and the version this program needs."""
    from . import migrate
    engine = _engine()
    have = migrate.current_revision(engine) if "alembic_version" in __import__("sqlalchemy").inspect(engine).get_table_names() else None
    console.print(f"Database version: {have or 'none (no migrations recorded)'}")
    console.print(f"Program needs:    {migrate.head_revision()}")
    console.print("Up to date." if have == migrate.head_revision() else "Run: trustbench db upgrade")


@db_app.command("check")
def db_check():
    """List what differs between the database and the models. Exit code 1 if anything does."""
    from . import migrate
    diffs = migrate.describe(migrate.differences(_engine()))
    if not diffs:
        console.print("[green]The database matches the models.[/green]")
        return
    for d in diffs:
        console.print(f"  {d}")
    raise typer.Exit(1)


@db_app.command("stamp")
def db_stamp(revision: str = typer.Argument("head")):
    """Record a version without changing tables. Use it only after you changed the tables by hand."""
    from . import migrate
    migrate.stamp(_engine(), revision)
    console.print(f"The database is recorded as version {revision}.")


@db_app.command("sql")
def db_sql(url: str = typer.Option("postgresql+psycopg://user:password@localhost/trustbench", help="The command uses only the type of the database. It connects to nothing.")):
    """Print the SQL that creates the schema, for a database administrator."""
    from . import migrate
    typer.echo(migrate.schema_sql(url))
