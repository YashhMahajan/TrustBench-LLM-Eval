"""The database schema and its versions.

  trustbench db upgrade   bring the database to the newest schema
  trustbench db status    show the version of the database and of the code
  trustbench db check     show where the database differs from the models

Commands that open the database (run, serve, worker, ...) call ensure_schema(). A new database is created.
An old one is upgraded. A database made before migrations existed is accepted if it matches the models.
"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .db import Base

MIGRATIONS = Path(__file__).parent / "migrations"
LOCK_ID = 7272742      # A number for the PostgreSQL advisory lock. It stops two processes from migrating at once.


class MigrationError(RuntimeError):
    pass


def _config(engine: Engine | None = None, url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url or (engine.url.render_as_string(hide_password=False).replace("%", "%%") if engine else "sqlite://"))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def differences(engine: Engine) -> list:
    """What differs between the database and the models. An empty list means they match."""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)


def describe(diffs: list) -> list[str]:
    out = []
    for d in diffs:
        items = d if isinstance(d, list) else [d]
        for x in items:
            kind = x[0]
            if kind in ("add_table", "remove_table"):
                out.append(f"{kind.replace('_', ' ')} {x[1].name}")
            elif kind in ("add_column", "remove_column"):
                out.append(f"{kind.replace('_', ' ')} {x[2]}.{x[3].name}")
            else:
                out.append(str(kind).replace("_", " ") + " " + ".".join(str(p) for p in x[1:4] if isinstance(p, (str,))))
    return out


def upgrade(engine: Engine, revision: str = "head") -> None:
    with engine.connect() as conn:
        locked = conn.dialect.name == "postgresql"
        if locked:
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_ID})
        try:
            cfg = _config(engine)
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, revision)
            conn.commit()
        finally:
            if locked:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})
                conn.commit()


def stamp(engine: Engine, revision: str = "head") -> None:
    cfg = _config(engine)
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.stamp(cfg, revision)
        conn.commit()


def ensure_schema(engine: Engine) -> str:
    """Make the database ready. Returns what was done: created, upgraded, stamped or current."""
    ours = set(Base.metadata.tables)
    have = set(inspect(engine).get_table_names())
    rev = current_revision(engine) if "alembic_version" in have else None
    head = head_revision()
    if not (ours & have):
        upgrade(engine)
        return "created"
    if rev is None:        # Made before migrations existed (with create_all). Accept it only if it matches the models.
        diffs = describe(differences(engine))
        if diffs:
            raise MigrationError("This database was made by an older TrustBench, before migrations existed, and it differs from the current schema: "
                                 + "; ".join(diffs[:6]) + ". Start with a new database, or add the missing columns by hand and run 'trustbench db stamp'.")
        stamp(engine)
        return "stamped"
    if rev == head:
        return "current"
    if rev not in {s.revision for s in ScriptDirectory.from_config(_config()).walk_revisions()}:
        raise MigrationError(f"The database is at version {rev}, which this TrustBench does not know. The database is newer than the program: update TrustBench.")
    upgrade(engine)
    return "upgraded"


def open_db(engine: Engine) -> sessionmaker:
    """Open a real database: check or migrate the schema, then give sessions."""
    ensure_schema(engine)
    return sessionmaker(engine, expire_on_commit=False)


def schema_sql(dialect_url: str = "postgresql+psycopg://user:password@localhost/trustbench", revision: str = "head") -> str:
    """The SQL that creates the schema, without a database connection. For a database administrator who runs it by hand."""
    import io
    cfg = _config(url=dialect_url)
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, revision, sql=True)
    return cfg.output_buffer.getvalue()
