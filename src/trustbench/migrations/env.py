"""How Alembic finds the models and the database. Used by trustbench db ..., not run by hand."""

from alembic import context
from sqlalchemy import create_engine, pool

from trustbench.db import Base

target_metadata = Base.metadata
config = context.config


def run_migrations_offline() -> None:
    """Write the SQL instead of running it (alembic ... --sql)."""
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        engine = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=pool.NullPool)
        with engine.connect() as connection:
            _run(connection)
    else:
        _run(connection)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
