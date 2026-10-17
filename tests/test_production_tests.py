import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from helpers import FnProvider
from trustbench import crypto, proposals, report
from trustbench.api import create_app
from trustbench.cli import app
from trustbench.dataprotect import update_policy
from trustbench.datasets import load_cases
from trustbench.db import ProposedCase, TraceEvaluation, init_db, make_engine
from trustbench.engine import RunOptions, run_eval
from trustbench.datasets import import_dataset
from trustbench.production_tests import PromoteError, flagged_traces, promote
from trustbench.providers import MockProvider
from trustbench.tenancy import create_api_key, create_workspace
from trustbench.traces import TraceIn, get_trace, ingest

RULE = {"type": "rule", "max_words": 5, "forbidden": ["sorry"]}


@pytest.fixture
def ws(factory, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    w = create_workspace(factory, "acme")
    update_policy(factory, w.id, store_content=True, online_evaluators=[RULE], sample_rate=1.0)
    return w


def add_eval(factory, ws, trace_id, evaluator="rule", passed=False, kind="measured", **details):
    pk = get_trace(factory, ws.id, trace_id)[0].id
    with factory.begin() as s:
        s.add(TraceEvaluation(trace_pk=pk, workspace_id=ws.id, evaluator=evaluator, rubric_version="1", kind=kind, passed=passed, score=0.0, details=details))


def test_flagged_traces_list_each_reason(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id="ok"), TraceIn(trace_id="err", status="error"), TraceIn(trace_id="bad", feedback=-1),
                            TraceIn(trace_id="chk"), TraceIn(trace_id="good", feedback=1), TraceIn(trace_id="evalfail")])
    add_eval(factory, ws, "chk")
    add_eval(factory, ws, "good", passed=True)
    add_eval(factory, ws, "evalfail", error=True)             # A broken evaluator is not a problem of the system.
    flags = {f.trace_id: f.reasons for f in flagged_traces(factory, ws.id)}
    assert flags == {"err": ["error"], "bad": ["bad_feedback"], "chk": ["check:rule (measured)"]}


def test_a_failed_rule_becomes_a_checked_test(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id="t1", name="support-bot", input="Where is my parcel?", output="Sorry, I cannot say.", model="m")])
    add_eval(factory, ws, "t1")
    p = promote(factory, ws.id, "t1")
    case = p.spec
    assert (p.dataset_name, p.method, p.workspace_id, p.expected_verified) == ("support-bot-production", "production", ws.id, True)
    assert case["input"] == {"prompt": "Where is my parcel?"} and case["task"] == "support-bot"
    assert case["evaluation"] == {"evaluators": [RULE]} and case["metadata"]["origin"] == "check:rule" and case["metadata"]["trace_id"] == "t1"


def test_context_becomes_a_grounded_case_and_a_judge_decision_is_not_checked(factory, ws):
    update_policy(factory, ws.id, online_evaluators=[RULE, {"type": "grounded_claims"}], online_judge="j:j")
    ingest(factory, ws.id, [TraceIn(trace_id="t1", name="bot", input="How long?", context="Refunds take 30 days.", output="60 days")])
    add_eval(factory, ws, "t1", "grounded_claims", kind="estimated")
    p = promote(factory, ws.id, "t1")
    assert p.spec["input"] == {"context": "Refunds take 30 days.", "question": "How long?"}
    assert p.spec["evaluation"]["evaluators"] == [{"type": "grounded_claims"}] and p.expected_verified is False
    with pytest.raises(proposals.ReviewError, match="answer written by a model"):
        proposals.approve(factory, [p.id])
    assert proposals.approve(factory, [p.id], confirm_unverified=True) == 1


def test_a_given_answer_makes_the_case_checked(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id="t1", name="bot", input="Capital of France?", output="Lyon", feedback=-1)])
    p = promote(factory, ws.id, "t1", answer="Paris", dataset="geo")
    assert p.dataset_name == "geo" and p.expected_verified and p.spec["expected"] == {"required_facts": ["Paris"]}
    assert p.spec["evaluation"]["evaluators"] == [{"type": "contains", "values": ["Paris"]}] and p.spec["metadata"]["origin"] == "bad_feedback"


def test_cases_that_cannot_be_made_are_refused_with_a_reason(factory, ws):
    ingest(factory, ws.id, [TraceIn(trace_id="noreason", input="q", feedback=-1)])
    with pytest.raises(PromoteError, match="no rule that says what is right"):
        promote(factory, ws.id, "noreason")
    with pytest.raises(PromoteError, match="No such trace"):
        promote(factory, ws.id, "missing")
    other = create_workspace(factory, "other")
    ingest(factory, ws.id, [TraceIn(trace_id="mine", input="q", feedback=-1)])
    with pytest.raises(PromoteError, match="No such trace"):
        promote(factory, other.id, "mine")                                   # Another workspace cannot reach it.


def test_traces_without_stored_text_cannot_become_tests(factory):
    w = create_workspace(factory, "plain")
    ingest(factory, w.id, [TraceIn(trace_id="t1", input="q", output="a", feedback=-1)])
    with pytest.raises(PromoteError, match="no stored text"):
        promote(factory, w.id, "t1", answer="x")


def test_personal_data_that_survives_redaction_refuses_the_case(factory, ws):
    update_policy(factory, ws.id, redact=False)
    ingest(factory, ws.id, [TraceIn(trace_id="t1", input="my card is 4111 1111 1111 1111", output="a", feedback=-1)])
    with pytest.raises(PromoteError, match="Personal data"):
        promote(factory, ws.id, "t1", answer="x")


def test_the_same_trace_or_a_near_copy_is_proposed_once(factory, ws):
    long_q = "Please look at my order and tell me the exact delivery date for the parcel that I bought last week"
    ingest(factory, ws.id, [TraceIn(trace_id="a1", name="bot", input=long_q, feedback=-1), TraceIn(trace_id="a2", name="bot", input=long_q + " today", feedback=-1)])
    promote(factory, ws.id, "a1", answer="x")
    with pytest.raises(PromoteError, match="already proposed"):
        promote(factory, ws.id, "a1", answer="x")
    with pytest.raises(PromoteError, match="very similar"):
        promote(factory, ws.id, "a2", answer="x")


def test_the_whole_loop_ends_in_a_dataset_that_fails_the_old_bug(factory, ws, tmp_path):
    ingest(factory, ws.id, [TraceIn(trace_id="t1", name="bot", input="Say hello in at most five words.", output="Sorry, hello to you and all of your family", feedback=-1)])
    add_eval(factory, ws, "t1")
    p = promote(factory, ws.id, "t1")
    proposals.approve(factory, [p.id])
    out = tmp_path / "reg.jsonl"
    n, _ = proposals.export_approved(factory, "bot-production", out)
    cases = load_cases(out)
    assert n == 1 and cases[0].metadata["source"] == "production"
    with factory() as s:
        v = import_dataset(s, "reg", cases)
    old_bug = MockProvider({"hello": "Sorry, hello to you and all of your family"})
    fixed = MockProvider({"hello": "Hello there, friend!"})
    for provider, expected in ((old_bug, False), (fixed, True)):
        rid = run_eval(factory, v.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n, p=provider: p)
        assert report.case_rows(factory, rid)[0]["passed"] is expected


def test_api_flagged_and_promote_need_the_right_roles(factory, ws):
    _, read = create_api_key(factory, ws.id, "r", "read")
    _, admin = create_api_key(factory, ws.id, "a", "admin")
    client = TestClient(create_app(factory))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    ingest(factory, ws.id, [TraceIn(trace_id="t1", name="bot", input="q?", output="a", feedback=-1)])
    assert client.get("/v1/flagged", headers=h(read)).json()["flagged"][0]["reasons"] == ["bad_feedback"]
    assert client.post("/v1/traces/t1/promote", json={"answer": "yes"}, headers=h(read)).status_code == 403
    r = client.post("/v1/traces/t1/promote", json={"answer": "yes"}, headers=h(admin))
    assert r.status_code == 201 and r.json()["answer_checked"] is True
    assert client.post("/v1/traces/t1/promote", json={"answer": "yes"}, headers=h(admin)).status_code == 409
    assert client.post("/v1/traces/zzz/promote", headers=h(admin)).status_code == 409
    assert client.get("/v1/audit", headers=h(admin)).json()["entries"][0]["action"] == "trace.promote"


def test_trace_commands(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    runner = CliRunner(env={"COLUMNS": "200"})
    factory = init_db(make_engine(url))
    w = create_workspace(factory, "default")
    update_policy(factory, w.id, store_content=True)
    ingest(factory, w.id, [TraceIn(trace_id="t1", name="bot", input="Where is it? ana@gmail.com", output="Unknown", feedback=-1, status="error", model="m")])
    assert "t1" in runner.invoke(app, ["traces", "list"]).output
    shown = runner.invoke(app, ["traces", "show", "t1", "--reveal"])
    assert "[EMAIL]" in shown.output and "gmail" not in shown.output and "Unknown" in shown.output
    assert "Unknown" not in runner.invoke(app, ["traces", "show", "t1"]).output          # Text only with --reveal.
    assert "bad_feedback" in runner.invoke(app, ["traces", "flagged"]).output
    done = runner.invoke(app, ["traces", "promote", "t1", "--answer", "Shipped"])
    assert done.exit_code == 0 and "Proposed test" in done.output
    assert runner.invoke(app, ["traces", "promote", "t1", "--answer", "Shipped"]).exit_code == 1
    assert "Deleted 0" in runner.invoke(app, ["traces", "purge"]).output
    assert runner.invoke(app, ["traces", "show", "nope"]).exit_code == 1
    with factory() as s:
        assert s.scalars(select(ProposedCase)).one().workspace_id == w.id
