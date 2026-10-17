import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from trustbench import crypto
from trustbench.api import create_app
from trustbench.cli import app
from trustbench.dataprotect import update_policy
from trustbench.db import TraceEvaluation, init_db, make_engine
from trustbench.monitor import monitor_summary
from trustbench.tenancy import create_api_key, create_workspace
from trustbench.traces import SpanIn, TraceIn, get_trace, ingest

NOW = dt.datetime(2025, 6, 30, 12, 0)


@pytest.fixture
def ws(factory):
    return create_workspace(factory, "acme")


def day(n, h=10):
    return NOW - dt.timedelta(days=n) + dt.timedelta(hours=h - 12)


def test_an_empty_window_says_so(factory, ws):
    d = monitor_summary(factory, ws.id, 7, now=NOW)
    assert d["traces"] == 0 and "no traces" in d["unmeasured"][0]


def test_measured_numbers(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id=f"t{i}", name="bot", started_at=day(i % 3), duration_ms=100.0 * (i + 1), status="error" if i < 2 else "ok",
                                    input="q", output="" if i == 3 else "a", model="gpt-4o-mini", input_tokens=1000, output_tokens=100,
                                    feedback=-1 if i == 0 else (1 if i == 5 else None)) for i in range(10)])
    d = monitor_summary(factory, ws.id, 7, now=NOW)
    m = d["measured"]
    assert d["traces"] == 10
    assert (m["error_rate"]["k"], m["error_rate"]["n"]) == (2, 10) and m["error_rate"]["ci"][0] < 0.2 < m["error_rate"]["ci"][1]
    assert m["latency_ms"]["p50"] == pytest.approx(550) and m["latency_ms"]["p95"] == pytest.approx(955)
    assert m["tokens"] == {"input": 10000, "output": 1000}
    assert m["cost_usd"]["total"] == pytest.approx(10 * (1000 * 0.15 + 100 * 0.60) / 1e6) and m["cost_usd"]["priced_share"] == 1.0
    assert (m["empty_answer_rate"]["k"], m["empty_answer_rate"]["n"]) == (1, 10)
    assert (m["user_feedback"]["bad"]["k"], m["user_feedback"]["bad"]["n"], m["user_feedback"]["rated_share"]) == (1, 2, 0.2)
    assert "do not stand for all traffic" in m["user_feedback"]["note"]


def test_unknown_cost_is_not_zero(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id="t", started_at=day(1))])
    assert monitor_summary(factory, ws.id, 7, now=NOW)["measured"]["cost_usd"]["total"] is None


def test_the_window_and_feature_filter(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id="new", name="a", started_at=day(1)), TraceIn(trace_id="old", name="a", started_at=day(20)),
                            TraceIn(trace_id="other", name="b", started_at=day(1))])
    assert monitor_summary(factory, ws.id, 7, now=NOW)["traces"] == 2
    assert monitor_summary(factory, ws.id, 7, "a", now=NOW)["traces"] == 1
    assert monitor_summary(factory, ws.id, 30, "a", now=NOW)["traces"] == 2


def test_daily_buckets(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id=f"t{i}", started_at=day(d), status="error" if i == 0 else "ok") for i, d in enumerate([0, 0, 1, 2, 2, 2])])
    daily = monitor_summary(factory, ws.id, 7, now=NOW)["daily"]
    assert [(x["date"], x["traces"]) for x in daily] == [("2025-06-28", 3), ("2025-06-29", 1), ("2025-06-30", 2)]
    assert daily[0]["error_rate"] == 0 and daily[2]["error_rate"] == 0.5


def add_eval(factory, ws, trace_id, evaluator, passed, kind, error=False):
    pk = get_trace(factory, ws.id, trace_id)[0].id
    with factory.begin() as s:
        s.add(TraceEvaluation(trace_pk=pk, workspace_id=ws.id, evaluator=evaluator, rubric_version="1", kind=kind, passed=passed, score=0.0,
                              details={"error": True} if error else {}))


def test_rule_checks_are_measured_and_judge_checks_are_estimated_and_the_gaps_are_listed(factory, ws, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    update_policy(factory, ws.id, online_evaluators=[{"type": "rule", "max_words": 5}])
    ingest(factory, ws.id, [TraceIn(trace_id=f"t{i}", started_at=day(1)) for i in range(10)])
    for i in range(4):
        add_eval(factory, ws, f"t{i}", "rule", passed=i != 0, kind="measured")
    add_eval(factory, ws, "t5", "grounded_claims", True, "estimated")
    add_eval(factory, ws, "t6", "llm_judge", True, "estimated", error=True)           # A broken evaluator is not counted.
    d = monitor_summary(factory, ws.id, 7, now=NOW)
    rule = d["measured"]["check:rule"]
    assert (rule["pass_rate"]["k"], rule["pass_rate"]["n"], rule["coverage"]) == (3, 4, 0.4)
    assert d["estimated"]["check:grounded_claims"]["pass_rate"]["n"] == 1 and "check:llm_judge" not in d["estimated"]
    gaps = " | ".join(d["unmeasured"])
    assert "helpfulness" in gaps and "answer correctness" in gaps and "groundedness" not in gaps and "format and rules" not in gaps


def test_without_any_online_evaluator_the_quality_gaps_say_so(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id="t", started_at=day(1))])
    gaps = monitor_summary(factory, ws.id, 7, now=NOW)["unmeasured"]
    assert sum("no online evaluator is set" in g for g in gaps) == 3 and any("answer correctness" in g for g in gaps)


def test_api_and_command(factory, ws, tmp_path, monkeypatch):
    from trustbench.db import utcnow
    ingest(factory, ws.id, [TraceIn(trace_id="t1", name="bot", duration_ms=300, output="a", status="error"), TraceIn(trace_id="t2", name="bot", duration_ms=500, output="b")])
    _, token = create_api_key(factory, ws.id, "r", "read")
    client = TestClient(create_app(factory))
    r = client.get("/v1/monitor?days=1&name=bot", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["traces"] == 2 and r.json()["measured"]["error_rate"]["k"] == 1
    assert client.get("/v1/monitor?days=0", headers={"Authorization": f"Bearer {token}"}).status_code == 422
    assert client.get("/v1/monitor").status_code == 401


def test_monitor_command(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    runner = CliRunner(env={"COLUMNS": "200"})
    factory = init_db(make_engine(url))
    w = create_workspace(factory, "default")
    assert "0 traces" in runner.invoke(app, ["monitor"]).output
    ingest(factory, w.id, [TraceIn(trace_id=f"t{i}", duration_ms=100.0 * (i + 1), status="error" if i == 0 else "ok", output="a") for i in range(10)])
    out = runner.invoke(app, ["monitor"])
    assert out.exit_code == 0 and "Error rate" in out.output and "Not measured" in out.output and "answer correctness" in out.output
    data = json.loads(runner.invoke(app, ["monitor", "--json"]).output)
    assert data["traces"] == 10 and data["measured"]["error_rate"]["k"] == 1
    assert runner.invoke(app, ["monitor", "nobody"]).exit_code == 1
