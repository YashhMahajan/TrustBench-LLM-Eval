import sqlite3

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from typer.testing import CliRunner

from trustbench import migrate
from trustbench.cli import app
from trustbench.db import Base, init_db, make_engine

runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def engine(tmp_path):
    return make_engine(f"sqlite:///{tmp_path}/m.db")


def test_a_new_database_is_created_by_the_migrations_and_matches_the_models(engine):
    assert migrate.ensure_schema(engine) == "created"
    assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    assert migrate.current_revision(engine) == migrate.head_revision()
    # If this fails, a model changed and nobody wrote a migration for it.
    assert migrate.describe(migrate.differences(engine)) == []


def test_ensure_schema_can_be_repeated(engine):
    assert migrate.ensure_schema(engine) == "created"
    assert migrate.ensure_schema(engine) == "current"


def test_the_first_migration_can_be_undone_and_done_again(engine):
    migrate.ensure_schema(engine)
    from alembic import command
    cfg = migrate._config(engine)
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
        conn.commit()
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    migrate.upgrade(engine)
    assert migrate.describe(migrate.differences(engine)) == []


def test_a_database_from_before_migrations_is_accepted_when_it_matches(engine):
    init_db(engine)                                     # What older versions did: create the tables directly.
    assert "alembic_version" not in inspect(engine).get_table_names()
    assert migrate.ensure_schema(engine) == "stamped"
    assert migrate.current_revision(engine) == migrate.head_revision()
    assert migrate.ensure_schema(engine) == "current"


def test_an_old_database_that_differs_is_refused_with_the_reason(engine, tmp_path):
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE jobs DROP COLUMN last_error"))
    with pytest.raises(migrate.MigrationError, match=r"older TrustBench.*last_error"):
        migrate.ensure_schema(engine)


def test_a_database_newer_than_the_program_is_refused(engine):
    migrate.ensure_schema(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = '9999'"))
    with pytest.raises(migrate.MigrationError, match="newer than the program"):
        migrate.ensure_schema(engine)


def test_commands_report_and_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    assert "created" in runner.invoke(app, ["db", "upgrade"]).output
    assert "already up to date" in runner.invoke(app, ["db", "upgrade"]).output
    status = runner.invoke(app, ["db", "status"]).output
    assert "0001" in status and "Up to date" in status
    assert runner.invoke(app, ["db", "check"]).exit_code == 0
    con = sqlite3.connect(f"{tmp_path}/c.db")
    con.execute("ALTER TABLE jobs DROP COLUMN last_error")
    con.commit()
    bad = runner.invoke(app, ["db", "check"])
    assert bad.exit_code == 1 and "jobs.last_error" in bad.output


def test_commands_that_open_the_database_create_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/n.db")
    assert runner.invoke(app, ["run", "--model", "mock:smoke"]).exit_code == 0
    assert migrate.current_revision(make_engine(f"sqlite:///{tmp_path}/n.db")) == migrate.head_revision()


def test_a_refused_database_gives_a_clear_message_and_exit_code(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/o.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    engine = make_engine(url)
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE jobs DROP COLUMN last_error"))
    out = runner.invoke(app, ["run", "--model", "mock:smoke"])
    assert out.exit_code == 1 and "older TrustBench" in out.output


# ---- PostgreSQL, without a PostgreSQL server --------------------------------------------------------

PG = postgresql.dialect()


def test_every_table_and_index_compiles_for_postgresql():
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=PG))
        assert "CREATE TABLE" in ddl
        for col in table.columns:
            if col.type.__class__.__name__ == "JSON" or "JSON" in str(col.type.__class__):
                assert "JSONB" in ddl, f"{table.name}.{col.name} is not JSONB on PostgreSQL"
        for index in table.indexes:
            assert "CREATE" in str(CreateIndex(index).compile(dialect=PG))


def test_the_migration_renders_as_postgresql_sql():
    sql = migrate.schema_sql()
    for name in Base.metadata.tables:
        assert f"CREATE TABLE {name}" in sql, name
    assert "JSONB" in sql and "alembic_version" in sql and ("SERIAL" in sql or "GENERATED" in sql)
    assert "BEGIN" in sql and "COMMIT" in sql


def test_the_important_queries_compile_for_postgresql():
    """The task claim and the retention purge are the queries most likely to differ between SQLite and PostgreSQL."""
    from sqlalchemy import delete, func, select, update
    from trustbench.db import JobRow, ProductionTrace, utcnow
    now = utcnow()
    queries = [
        select(JobRow).where(((JobRow.state == "queued") & (JobRow.run_after <= now)) | ((JobRow.state == "running") & (JobRow.lease_expires_at < now))).order_by(JobRow.id).limit(1),
        update(JobRow).where(JobRow.id == 1, JobRow.state == "queued", JobRow.attempts == 0).values(state="running", attempts=1),
        delete(ProductionTrace).where(ProductionTrace.expires_at < now),
        select(JobRow.state, func.count()).group_by(JobRow.state),
    ]
    for q in queries:
        assert str(q.compile(dialect=PG, compile_kwargs={"literal_binds": False}))


def test_db_sql_command_prints_postgresql_ddl():
    out = runner.invoke(app, ["db", "sql"])
    assert out.exit_code == 0 and "CREATE TABLE production_traces" in out.output and "JSONB" in out.output
