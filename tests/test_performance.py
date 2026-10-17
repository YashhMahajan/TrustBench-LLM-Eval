"""Performance rules. They count work (calls, queries, imports) instead of seconds, so they do not depend on the machine."""

import subprocess
import sys
import time

from sqlalchemy import event

from trustbench import quality, report
from trustbench.datasets import CaseSpec
from trustbench.rundata import Record, RunData
from test_engine import go
from trustbench.providers import MockProvider


def test_choosing_a_model_is_done_once_not_once_per_record(monkeypatch):
    records = [Record(case_id=f"c{i}", model="m:a" if i % 2 else "m:b") for i in range(5000)]
    rd = RunData("r", "complete", "d", "h", 0.0, records)
    calls = []
    real = RunData.pick_model
    monkeypatch.setattr(RunData, "pick_model", lambda self, model=None: calls.append(1) or real(self, model))
    assert len(rd.for_model("m:a")) == 2500
    assert len(calls) == 1          # It used to be 5000 calls, each one scanning all records.


def test_the_list_of_models_is_computed_once():
    rd = RunData("r", "complete", "d", "h", 0.0, [Record(model="m:a"), Record(model="m:b")])
    assert rd.models is rd.models


def count_statements(factory):
    seen = []
    event.listen(factory.kw["bind"], "before_cursor_execute", lambda conn, cur, stmt, params, ctx, many: seen.append(stmt))
    return seen


def test_case_rows_use_a_fixed_number_of_queries(factory, cases):
    from trustbench.datasets import import_dataset
    small = [CaseSpec(id=f"s{i}", input=f"Question {i}", expected={"answer": "yes"}) for i in range(10)]
    large = [CaseSpec(id=f"l{i}", input=f"Question {i}", expected={"answer": "yes"}) for i in range(200)]
    counts = []
    for name, data in (("small", small), ("large", large)):
        with factory() as s:
            v = import_dataset(s, name, data)
        rid = go(factory, v, MockProvider({"Question": "yes"}))
        seen = count_statements(factory)
        rows = report.case_rows(factory, rid)
        counts.append(len(seen))
        assert len(rows) == len(data)
    assert counts[0] == counts[1] and counts[1] <= 8        # It was one query for every execution (an "N+1" problem).


def test_the_duplicate_check_scales_to_many_cases():
    """4000 questions made from one template share many word groups. The check must not compare every pair."""
    cases = [CaseSpec(id=f"c{i}", input=f"Please answer the following question about the topic number {i} in the catalogue", expected={"answer": str(i)})
             for i in range(4000)]
    cases.append(CaseSpec(id="twin", input="Please answer the following question about the topic number 17 in the catalogue today", expected={"answer": "17"}))
    t0 = time.perf_counter()
    issues = quality.check_duplicates_and_conflicts(cases)
    assert time.perf_counter() - t0 < 4.0                   # It took about 4 seconds for 20000 cases before, and grew with the square.
    assert any(i.code == "near_duplicate" and "twin" in i.case_ids for i in issues)


def test_starting_the_command_line_does_not_load_the_heavy_libraries():
    code = ("import sys; import trustbench.cli; "
            "heavy = [m for m in ('sqlalchemy', 'pydantic', 'fastapi', 'alembic', 'cryptography', 'httpx', 'yaml', 'uvicorn') if m in sys.modules]; "
            "print(','.join(heavy))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "", out.stdout + out.stderr


def test_help_is_fast_and_commands_still_load():
    t0 = time.perf_counter()
    out = subprocess.run([sys.executable, "-c", "from trustbench.cli import app; app(['--help'])"], capture_output=True, text=True, timeout=60)
    assert "run" in out.stdout and "selftest" in out.stdout and "gate" in out.stdout
    assert time.perf_counter() - t0 < 1.5                  # It took 1.15 s on a fast machine before. A slow one is allowed some room.


def test_a_file_database_uses_write_ahead_logging_and_enforces_foreign_keys(tmp_path):
    from sqlalchemy import text
    from trustbench.db import make_engine
    engine = make_engine(f"sqlite:///{tmp_path}/w.db")
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert c.execute(text("PRAGMA busy_timeout")).scalar() >= 5000


def test_ingesting_a_batch_uses_few_statements(factory):
    from trustbench.tenancy import create_workspace
    from trustbench.traces import SpanIn, TraceIn, ingest
    ws = create_workspace(factory, "w")
    batch = [TraceIn(trace_id=f"t{i}", spans=[SpanIn(name="a"), SpanIn(name="b")]) for i in range(300)]
    seen = count_statements(factory)
    ingest(factory, ws.id, batch)
    assert len(seen) <= 25, len(seen)                      # It used one statement for every trace and every span.


# ---- Indexes: ask the database how it will run the query, and check it uses an index -----------------

def _plan(engine, sql):
    from sqlalchemy import text
    with engine.connect() as c:
        return " | ".join(r[3] for r in c.execute(text("EXPLAIN QUERY PLAN " + sql)))


def test_the_main_queries_use_an_index_and_not_a_table_scan(tmp_path):
    from trustbench.db import init_db, make_engine
    engine = make_engine(f"sqlite:///{tmp_path}/plan.db")
    init_db(engine)
    expected = {
        "SELECT * FROM jobs WHERE state='queued' AND run_after<='2026-01-01' ORDER BY run_after, id LIMIT 1":
            "ix_jobs_state_run_after_id",
        "SELECT * FROM evaluation_runs WHERE workspace_id='w' ORDER BY created_at DESC LIMIT 20":
            "ix_evaluation_runs_workspace_created",
        "SELECT * FROM test_executions WHERE run_id='r' AND status='pending'":
            "ix_test_executions_run_status",
        "SELECT * FROM production_traces WHERE workspace_id='w' AND started_at>='2026-01-01'":
            "ix_production_traces_workspace_started",
    }
    for sql, index in expected.items():
        plan = _plan(engine, sql)
        assert index in plan, f"{sql}\n -> {plan}"
        assert "SCAN" not in plan, plan


def test_an_index_that_a_composite_index_already_covers_is_not_kept():
    from trustbench.db import Base
    for table in Base.metadata.sorted_tables:
        indexes = [tuple(c.name for c in i.columns) for i in table.indexes]
        for a in indexes:
            for b in indexes:
                assert not (a != b and b[:len(a)] == a), f"{table.name}: {a} is covered by {b}"
