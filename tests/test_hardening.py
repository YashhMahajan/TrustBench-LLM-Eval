import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from test_engine import ANSWERS, go
from trustbench import baselines, migrate
from trustbench.api import create_app
from trustbench.cli import app
from trustbench.db import ApiKey, init_db, make_engine, utcnow
from trustbench.gate import evaluate_gate
from trustbench.policy import ReleasePolicy, Rule
from trustbench.providers import MockProvider
from trustbench.ratelimit import RateLimiter
from trustbench.report_export import build_report, to_markdown
from trustbench.rundata import load_run
from trustbench.tenancy import TenancyError, authenticate, create_api_key, create_workspace

runner = CliRunner(env={"COLUMNS": "220"})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---- Key expiry ---------------------------------------------------------------------------------

def test_a_key_works_until_it_expires_and_then_gets_the_answer_of_a_wrong_key(factory):
    ws = create_workspace(factory, "acme")
    row, token = create_api_key(factory, ws.id, "short", "write", expires_in_days=1)
    assert row.expires_at and dt.timedelta(hours=23) < row.expires_at - utcnow() < dt.timedelta(hours=25)
    assert authenticate(factory, token) is not None
    with factory.begin() as s:
        s.get(ApiKey, row.id).expires_at = utcnow() - dt.timedelta(seconds=1)
    assert authenticate(factory, token) is None
    client = TestClient(create_app(factory))
    r = client.get("/v1/traces", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json() == {"error": "A valid API key is needed."}


def test_keys_without_an_end_date_and_bad_end_dates(factory):
    ws = create_workspace(factory, "acme")
    row, token = create_api_key(factory, ws.id, "forever")
    assert row.expires_at is None and authenticate(factory, token)
    for bad in (0, -3):
        with pytest.raises(TenancyError, match="in the future"):
            create_api_key(factory, ws.id, "x", expires_in_days=bad)


def test_key_commands_show_the_end_date_and_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/k.db")
    runner.invoke(app, ["workspace", "create", "acme"])
    made = runner.invoke(app, ["key", "create", "--workspace", "acme", "--expires-days", "30"])
    assert made.exit_code == 0 and "valid until" in made.output
    assert "with no end date" in runner.invoke(app, ["key", "create", "--workspace", "acme"]).output
    factory = init_db(make_engine(f"sqlite:///{tmp_path}/k.db"))
    with factory.begin() as s:
        s.get(ApiKey, 1).expires_at = utcnow() - dt.timedelta(days=1)
    listing = runner.invoke(app, ["key", "list"]).output
    assert "expired" in listing and "active" in listing
    assert runner.invoke(app, ["key", "create", "--workspace", "acme", "--expires-days", "0"]).exit_code == 1


def test_the_expiry_migration_keeps_existing_keys(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/mig.db")
    migrate.upgrade(engine, "0001")
    cols = {c["name"] for c in inspect(engine).get_columns("api_keys")}
    assert "expires_at" not in cols
    import hashlib
    token = "tb_" + "a" * 43
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO workspaces (id, name, settings, created_at) VALUES ('w1', 'old', '{}', '2025-01-01')"))
        conn.execute(text("INSERT INTO api_keys (workspace_id, name, prefix, key_hash, role, created_at) VALUES ('w1', 'old key', :p, :h, 'read', '2025-01-01')"),
                     {"p": token[:11], "h": hashlib.sha256(token.encode()).hexdigest()})
    assert migrate.ensure_schema(engine) == "upgraded"
    assert "expires_at" in {c["name"] for c in inspect(engine).get_columns("api_keys")}
    assert authenticate(sessionmaker(engine), token).role == "read"                  # The old key still works. It never expires.
    assert migrate.describe(migrate.differences(engine)) == []
    from alembic import command
    cfg = migrate._config(engine)
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0001")
        conn.commit()
    assert "expires_at" not in {c["name"] for c in inspect(engine).get_columns("api_keys")}


# ---- Rate limits -------------------------------------------------------------------------------------

def test_the_limiter_counts_a_sliding_window():
    clock = Clock()
    lim = RateLimiter(3, 60, clock)
    assert [lim.hit("a")[0] for _ in range(3)] == [True, True, True]
    ok, wait = lim.hit("a")
    assert not ok and 59 <= wait <= 60
    assert lim.hit("b")[0]                                   # Another key has its own budget.
    clock.t += 30
    assert not lim.hit("a")[0]
    clock.t += 31
    assert lim.hit("a")[0] and lim.count("a") == 1


def test_a_limit_of_zero_means_no_limit_and_old_keys_are_forgotten():
    clock = Clock()
    assert all(RateLimiter(0, 60, clock).hit("a")[0] for _ in range(1000))
    lim = RateLimiter(5, 60, clock)
    for i in range(21000):
        lim.hit(f"k{i}")
        clock.t += 0.01
    assert len(lim._hits) < 21000


@pytest.fixture
def api(factory):
    ws = create_workspace(factory, "acme")
    keys = [create_api_key(factory, ws.id, f"k{i}", "read")[1] for i in range(2)]
    return factory, keys


def get(client, token, path="/v1/traces"):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def test_a_key_that_sends_too_much_is_told_to_wait(api):
    factory, (k1, k2) = api
    clock = Clock()
    client = TestClient(create_app(factory, rate_limit=3, clock=clock))
    assert [get(client, k1).status_code for _ in range(3)] == [200, 200, 200]
    r = get(client, k1)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "too many requests" in r.json()["error"]
    assert get(client, k2).status_code == 200                      # Another key stays free.
    assert client.get("/v1/health").status_code == 200             # The health check is free.
    clock.t += 61
    assert get(client, k1).status_code == 200


def test_the_limit_comes_from_the_environment_by_default(api, monkeypatch):
    factory, (k1, _) = api
    monkeypatch.setenv("TRUSTBENCH_RATE_LIMIT", "2")
    client = TestClient(create_app(factory))
    assert [get(client, k1).status_code for _ in range(3)] == [200, 200, 429]


def test_repeated_wrong_keys_lock_the_address_for_a_while(api):
    factory, (k1, _) = api
    clock = Clock()
    client = TestClient(create_app(factory, rate_limit=100, bad_key_limit=3, clock=clock))
    assert [get(client, "tb_wrong").status_code for _ in range(3)] == [401, 401, 401]
    locked = get(client, k1)                                       # The server refuses even a good key from a locked address.
    assert locked.status_code == 429 and "wrong keys" in locked.json()["error"] and int(locked.headers["retry-after"]) >= 1
    clock.t += 61
    assert get(client, k1).status_code == 200


def test_good_requests_do_not_count_as_wrong_keys(api):
    factory, (k1, _) = api
    client = TestClient(create_app(factory, rate_limit=100, bad_key_limit=3))
    assert all(get(client, k1).status_code == 200 for _ in range(10))


# ---- The run report ------------------------------------------------------------------------------------

@pytest.fixture
def reported(factory, version):
    rid = go(factory, version, MockProvider(ANSWERS))
    policy = ReleasePolicy(name="gate", min_cases=2, critical_failures_allowed=None, overall=Rule(minimum_score=0.5))
    baselines.save_decision(factory, rid, "mock:m", None, policy, evaluate_gate(policy, load_run(factory, rid), "mock:m"))
    return factory, rid


def test_the_markdown_report_has_the_numbers_the_reasons_and_how_to_repeat(reported):
    factory, rid = reported
    md = to_markdown(build_report(factory, rid))
    for text_ in ("# Run report", f"`{rid}`", "### Pass rate", "95% interval", "| **All cases** | 3 |", "### Speed, errors and cost",
                  "### How cases failed", "wrong answer", "## Release decisions", "## How to repeat this run", "does not prove that a system is safe"):
        assert text_ in md, text_


def test_the_json_report_has_the_same_content(reported):
    factory, rid = reported
    data = build_report(factory, rid)
    assert data["meta"]["run_id"] == rid and data["models"]["mock:m"]["summary"]["overall"]["n"] == 3
    assert data["decisions"][0]["policy"] == "gate" and data["models"]["mock:m"]["failures"]["failed_cases"] == 1
    import json
    json.dumps(data)                                                   # You can write it as JSON.


def test_report_for_one_model_and_for_unknown_models(factory, version):
    rid = go(factory, version, MockProvider(ANSWERS), specs=("mock:m1", "mock:m2"))
    assert list(build_report(factory, rid, "mock:m2")["models"]) == ["mock:m2"] and len(build_report(factory, rid)["models"]) == 2
    with pytest.raises(ValueError, match="no model"):
        build_report(factory, rid, "mock:zzz")


def test_export_command(tmp_path, monkeypatch, cases):
    from trustbench.datasets import import_dataset
    url = f"sqlite:///{tmp_path}/e.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    factory = init_db(make_engine(url))
    with factory() as s:
        version = import_dataset(s, "t", cases)
    rid = go(factory, version, MockProvider(ANSWERS))
    out = tmp_path / "r.md"
    assert runner.invoke(app, ["export", rid, "--out", str(out)]).exit_code == 0 and "# Run report" in out.read_text()
    js = tmp_path / "r.json"
    assert runner.invoke(app, ["export", rid, "--format", "json", "--out", str(js)]).exit_code == 0 and '"run_id"' in js.read_text()
    assert runner.invoke(app, ["export", "nope"]).exit_code == 1
    assert runner.invoke(app, ["export", rid, "--format", "pdf"]).exit_code == 1


def test_report_route_needs_a_key_of_the_same_workspace(reported):
    factory, rid = reported
    from trustbench.db import EvaluationRun
    with factory.begin() as s:
        ws = create_workspace(factory, "mine")
        s.get(EvaluationRun, rid).workspace_id = ws.id
    _, mine = create_api_key(factory, ws.id, "k", "read")
    _, other = create_api_key(factory, create_workspace(factory, "other").id, "k", "read")
    client = TestClient(create_app(factory))
    ok = get(client, mine, f"/v1/runs/{rid}/report")
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/markdown") and "attachment" in ok.headers["content-disposition"] and "# Run report" in ok.text
    assert get(client, mine, f"/v1/runs/{rid}/report?format=json").json()["meta"]["run_id"] == rid
    assert get(client, other, f"/v1/runs/{rid}/report").status_code == 404
    assert get(client, mine, f"/v1/runs/{rid}/report?model=nope:x").status_code == 422
    assert client.get(f"/v1/runs/{rid}/report").status_code == 401
