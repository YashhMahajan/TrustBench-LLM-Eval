from typer.testing import CliRunner

from trustbench import crypto
from trustbench.cli import app
from trustbench.tenancy import authenticate
from trustbench.db import init_db, make_engine

runner = CliRunner(env={"COLUMNS": "200"})


def test_workspace_and_key_flow(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    assert runner.invoke(app, ["workspace", "create", "acme"]).exit_code == 0
    assert runner.invoke(app, ["workspace", "create", "acme"]).exit_code == 1          # Names are unique.
    made = runner.invoke(app, ["key", "create", "--workspace", "acme", "--role", "write", "--name", "ci"])
    assert made.exit_code == 0 and "only time" in made.output
    token = next(w for w in made.output.split() if w.startswith("tb_"))
    factory = init_db(make_engine(url))
    assert authenticate(factory, token).role == "write"
    listing = runner.invoke(app, ["key", "list"])
    assert "ci" in listing.output and token not in listing.output and token[:11] in listing.output     # Only the start is shown.
    assert runner.invoke(app, ["key", "revoke", "1"]).exit_code == 0
    assert authenticate(factory, token) is None
    assert runner.invoke(app, ["key", "create", "--workspace", "nope"]).exit_code == 1
    assert runner.invoke(app, ["key", "create", "--workspace", "acme", "--role", "boss"]).exit_code == 1


def test_workspace_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.delenv("TRUSTBENCH_SECRET_KEY", raising=False)
    runner.invoke(app, ["workspace", "create", "acme"])
    ok = runner.invoke(app, ["workspace", "set", "acme", "--retention-days", "7", "--sample-rate", "0.5"])
    assert ok.exit_code == 0 and '"retention_days": 7' in ok.output
    no_key = runner.invoke(app, ["workspace", "set", "acme", "--store-content"])
    assert no_key.exit_code == 1 and "encryption key" in no_key.output
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    both = runner.invoke(app, ["workspace", "set", "acme", "--store-content", "--no-redact"])
    assert both.exit_code == 0 and "without redaction" in both.output
    assert "yes" in runner.invoke(app, ["workspace", "list"]).output
    assert runner.invoke(app, ["workspace", "set", "acme", "--sample-rate", "5"]).exit_code == 1
    assert runner.invoke(app, ["workspace", "set", "nobody"]).exit_code == 1


def test_secret_key_command_makes_a_working_key(monkeypatch):
    out = runner.invoke(app, ["secrets", "generate-key"])
    key = out.output.split()[0]
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", key)
    assert crypto.decrypt_text(crypto.encrypt_text("x")) == "x"


def test_jobs_commands_and_worker_wiring(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    monkeypatch.delenv("TRUSTBENCH_REDIS_URL", raising=False)
    factory = init_db(make_engine(url))
    from trustbench.jobs import DbQueue
    q = DbQueue(factory)
    q.enqueue("never_handled", {}, max_attempts=1)
    job = q.claim("w", ["never_handled"])
    q.fail(job.id, "ValueError: broken thing")
    assert "dead 1" in runner.invoke(app, ["jobs", "status"]).output
    dead = runner.invoke(app, ["jobs", "dead"])
    assert "never_handled" in dead.output and "broken thing" in dead.output
    assert runner.invoke(app, ["jobs", "retry", job.id]).exit_code == 0
    assert runner.invoke(app, ["jobs", "retry", job.id]).exit_code == 1
    assert "queued 1" in runner.invoke(app, ["jobs", "status"]).output


def test_drift_command(tmp_path, monkeypatch):
    import datetime as dt
    import random
    from trustbench.tenancy import create_workspace
    from trustbench.traces import TraceIn, ingest
    from trustbench.db import utcnow
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    factory = init_db(make_engine(url))
    ws = create_workspace(factory, "acme")
    rng = random.Random(0)
    now = utcnow()
    ingest(factory, ws.id, [TraceIn(trace_id=f"r{i}", started_at=now - dt.timedelta(days=rng.uniform(8, 36)), duration_ms=rng.gauss(800, 100)) for i in range(200)])
    ingest(factory, ws.id, [TraceIn(trace_id=f"c{i}", started_at=now - dt.timedelta(days=rng.uniform(0, 6)), duration_ms=rng.gauss(1500, 100)) for i in range(80)])
    out = runner.invoke(app, ["drift", "acme"])
    assert out.exit_code == 0 and "latency (ms)" in out.output and "large" in out.output and "not proof" in out.output
    assert runner.invoke(app, ["drift", "nobody"]).exit_code == 1
    assert "At least 30" in runner.invoke(app, ["drift", "acme", "--name", "none-such"]).output
