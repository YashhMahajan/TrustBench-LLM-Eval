import datetime as dt
import random

import pytest

from trustbench.drift import MIN_TRACES, drift_report
from trustbench.tenancy import create_workspace
from trustbench.traces import SpanIn, TraceIn, ingest

NOW = dt.datetime(2025, 6, 30, 12, 0)


@pytest.fixture
def ws(factory):
    return create_workspace(factory, "acme")


def fill(factory, ws, days_ago_range, n, seed, **gen):
    """Traces spread over a window. gen maps a field to a function of the random generator."""
    rng = random.Random(seed)
    lo, hi = days_ago_range
    batch = []
    for i in range(n):
        when = NOW - dt.timedelta(days=rng.uniform(lo, hi))
        kw = {k: f(rng) for k, f in gen.items()}
        batch.append(TraceIn(trace_id=f"{seed}-{i}", name="bot", started_at=when, **kw))
    for i in range(0, n, 400):
        ingest(factory, ws.id, batch[i:i + 400])


BASE = dict(duration_ms=lambda r: r.gauss(800, 120), input_tokens=lambda r: int(r.gauss(300, 50)), output_tokens=lambda r: int(r.gauss(150, 30)),
            model=lambda r: "m1", status=lambda r: "error" if r.random() < 0.02 else "ok")


def report(factory, ws, **kw):
    return drift_report(factory, ws.id, 7, 30, now=NOW, **kw)


def test_a_steady_system_shows_no_drift(factory, ws):
    fill(factory, ws, (7, 37), 400, 1, **BASE)
    fill(factory, ws, (0, 7), 150, 2, **BASE)
    rep = report(factory, ws)
    assert (rep.reference_traces, rep.current_traces) == (400, 150)
    assert rep.drifted == [] and rep.warnings == []
    assert {f.feature for f in rep.findings} >= {"latency (ms)", "output tokens", "error rate"}
    assert "model" not in {f.feature for f in rep.findings}        # One model in both windows: nothing to compare.


def test_a_latency_shift_is_found_with_its_numbers(factory, ws):
    fill(factory, ws, (7, 37), 400, 1, **BASE)
    fill(factory, ws, (0, 7), 150, 2, **{**BASE, "duration_ms": lambda r: r.gauss(1400, 150)})
    rep = report(factory, ws)
    [f] = [f for f in rep.drifted]
    assert f.feature == "latency (ms)" and f.level == "large" and f.p_value < 1e-6 and f.statistic > 0.25
    assert f.reference.startswith("median 79") and f.current.startswith("median 139")


def test_a_new_model_and_a_higher_error_rate_are_found(factory, ws):
    fill(factory, ws, (7, 37), 400, 1, **BASE)
    fill(factory, ws, (0, 7), 150, 2, **{**BASE, "model": lambda r: "m2" if r.random() < 0.6 else "m1", "status": lambda r: "error" if r.random() < 0.15 else "ok"})
    drifted = {f.feature: f for f in report(factory, ws).drifted}
    assert "model" in drifted and "error rate" in drifted
    assert drifted["model"].level == "large" and "m2" in drifted["model"].current
    assert "2." in drifted["error rate"].reference and "of 400" in drifted["error rate"].reference


def test_tool_use_and_steps_are_compared(factory, ws):
    plain = lambda r: [SpanIn(name="m", kind="model")]
    busy = lambda r: [SpanIn(name="m", kind="model")] + [SpanIn(name="t", kind="tool") for _ in range(r.randint(2, 4))]
    fill(factory, ws, (7, 37), 200, 1, **BASE, spans=plain)
    fill(factory, ws, (0, 7), 100, 2, **BASE, spans=busy)
    names = {f.feature for f in report(factory, ws).drifted}
    assert {"tool calls per trace", "steps per trace"} <= names


def test_not_enough_data_gives_a_warning_and_no_findings(factory, ws):
    fill(factory, ws, (7, 37), 400, 1, **BASE)
    fill(factory, ws, (0, 7), MIN_TRACES - 1, 2, **BASE)
    rep = report(factory, ws)
    assert rep.findings == [] and "current window has 29" in rep.warnings[0]
    assert "reference window has 0" in drift_report(factory, create_workspace(factory, "empty").id, now=NOW).warnings[0]


def test_a_name_filter_limits_the_comparison(factory, ws):
    fill(factory, ws, (7, 37), 300, 1, **BASE)
    fill(factory, ws, (0, 7), 100, 2, **{**BASE, "duration_ms": lambda r: r.gauss(1400, 150)})
    assert report(factory, ws, name="other-feature").warnings and report(factory, ws, name="bot").drifted


def test_other_workspaces_are_not_mixed_in(factory, ws):
    other = create_workspace(factory, "other")
    fill(factory, ws, (7, 37), 300, 1, **BASE)
    fill(factory, ws, (0, 7), 100, 2, **BASE)
    fill(factory, other, (0, 7), 400, 3, **{**BASE, "duration_ms": lambda r: r.gauss(5000, 100)})
    assert report(factory, ws).drifted == []


def test_many_features_of_pure_noise_rarely_raise_an_alarm(factory):
    alarms = 0
    for seed in range(12):
        w = create_workspace(factory, f"w{seed}")
        fill(factory, w, (7, 37), 150, seed * 10 + 1, **BASE)
        fill(factory, w, (0, 7), 60, seed * 10 + 2, **BASE)
        alarms += bool(report(factory, w).drifted)
    assert alarms <= 2


def test_online_check_rates_are_compared(factory, ws):
    from sqlalchemy import select
    from trustbench.db import ProductionTrace, TraceEvaluation
    fill(factory, ws, (7, 37), 200, 1, **BASE)
    fill(factory, ws, (0, 7), 100, 2, **BASE)
    with factory.begin() as s:
        for row in s.scalars(select(ProductionTrace)):
            recent = row.started_at >= NOW - dt.timedelta(days=7)
            fail = random.Random(row.id).random() < (0.5 if recent else 0.05)
            s.add(TraceEvaluation(trace_pk=row.id, workspace_id=ws.id, evaluator="rule", rubric_version="1", kind="measured", passed=not fail, score=0.0))
    names = {f.feature for f in report(factory, ws).drifted}
    assert "online check fails: rule (measured)" in names


def test_drift_through_the_api_and_the_command(factory, ws, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from trustbench.api import create_app
    from trustbench.tenancy import create_api_key
    fill(factory, ws, (7, 37), 300, 1, **BASE)
    fill(factory, ws, (0, 7), 100, 2, **{**BASE, "duration_ms": lambda r: r.gauss(1400, 150)})
    _, token = create_api_key(factory, ws.id, "r", "read")
    # The API compares with the real current time, so move the data forward to be inside its windows.
    from sqlalchemy import select
    from trustbench.db import ProductionTrace, utcnow
    shift = utcnow() - NOW
    with factory.begin() as s:
        for row in s.scalars(select(ProductionTrace)):
            row.started_at = row.started_at + shift
    r = TestClient(create_app(factory)).get("/v1/drift", headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    assert r.status_code == 200 and "signal to investigate" in body["note"]
    assert any(f["feature"] == "latency (ms)" and f["drifted"] for f in body["findings"])
    assert TestClient(create_app(factory)).get("/v1/drift?current_days=0", headers={"Authorization": f"Bearer {token}"}).status_code == 422
