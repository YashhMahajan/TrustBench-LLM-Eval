import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from helpers import FnProvider
from trustbench import crypto
from trustbench.api import create_app
from trustbench.dataprotect import PolicyProblem, update_policy
from trustbench.db import JobRow
from trustbench.handlers import default_handlers
from trustbench.jobs import DbQueue, run_worker
from trustbench.online import check_specs, run_online_evaluation, sanitize_details
from trustbench.tenancy import create_api_key, create_workspace
from trustbench.traces import TraceIn, get_trace, ingest

RULE = {"type": "rule", "max_words": 5, "forbidden": ["sorry"]}


@pytest.fixture
def key(monkeypatch):
    k = crypto.generate_key()
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", k)
    return k


def judge_provider(label="supported"):
    def fn(prompt):
        if "List the factual claims" in prompt:
            return json.dumps({"claims": ["Refunds take 30 days."]})
        if "Check the claim" in prompt:
            return json.dumps({"label": label, "quote": "Refunds take 30 days" if label == "supported" else "", "rationale": "r", "confidence": 0.9})
        return json.dumps({"score": 3, "rationale": "clear"})
    return FnProvider(fn)


@pytest.fixture
def setup(factory, key):
    ws = create_workspace(factory, "acme")
    _, token = create_api_key(factory, ws.id, "w", "write")
    _, admin = create_api_key(factory, ws.id, "a", "admin")
    client = TestClient(create_app(factory))
    update_policy(factory, ws.id, sample_rate=1.0, online_evaluators=[RULE, {"type": "grounded_claims"}, {"type": "llm_judge", "rubric": "helpfulness"}],
                  online_judge="judge:j")
    provider = judge_provider()
    handlers = default_handlers(factory, provider_factory=lambda name: provider)
    return factory, ws, client, {"Authorization": f"Bearer {token}"}, {"Authorization": f"Bearer {admin}"}, handlers, provider


def send(client, headers, **trace):
    return client.post("/v1/traces", json={"traces": [trace]}, headers=headers)


def drain(factory, handlers):
    return run_worker(DbQueue(factory), handlers, concurrency=1, idle_exit=True, poll_interval=0.01)


def test_policy_checks_the_evaluators():
    assert check_specs([RULE], None) is None
    assert "cannot run without a reference" in check_specs([{"type": "exact_match"}], None)
    assert "needs online_judge" in check_specs([{"type": "llm_judge"}], None)
    assert "provider:model" in check_specs([RULE], "justamodel")
    assert check_specs([{"type": "grounded_claims"}], "a:b") is None


def test_policy_needs_an_encryption_key_for_online_evaluation(factory, monkeypatch):
    ws = create_workspace(factory, "w")
    monkeypatch.delenv("TRUSTBENCH_SECRET_KEY", raising=False)
    with pytest.raises(PolicyProblem, match="encrypted tasks"):
        update_policy(factory, ws.id, online_evaluators=[RULE])
    with pytest.raises(PolicyProblem, match="needs online_judge"):
        monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
        update_policy(factory, ws.id, online_evaluators=[{"type": "llm_judge"}])


def test_sampled_traces_are_checked_by_a_worker_and_results_say_how_sure_they_are(setup):
    factory, ws, client, write, admin, handlers, provider = setup
    r = send(client, write, trace_id="t1", name="bot", input="How long do refunds take?", context="Refunds take 30 days.", output="It takes 30 days, sorry for the wait.")
    assert r.status_code == 202
    assert get_trace(factory, ws.id, "t1")[0].eval_state == "pending"
    assert drain(factory, handlers) == 1
    detail = client.get("/v1/traces/t1", headers=admin).json()
    assert detail["eval_state"] == "done"
    ev = {e["evaluator"]: e for e in detail["evaluations"]}
    assert set(ev) == {"rule", "grounded_claims", "llm_judge"}
    assert (ev["rule"]["kind"], ev["rule"]["passed"]) == ("measured", False) and "sorry" in ev["rule"]["rationale"] and "words" in ev["rule"]["rationale"]
    assert (ev["grounded_claims"]["kind"], ev["grounded_claims"]["passed"]) == ("estimated", True)
    assert ev["llm_judge"]["kind"] == "estimated"
    assert "claims" not in ev["grounded_claims"]["details"] and ev["grounded_claims"]["details"]["metrics"]["supported"] == 1


def test_the_text_waits_encrypted_and_is_gone_when_the_task_is_done(setup):
    factory, ws, client, write, admin, handlers, _ = setup
    send(client, write, trace_id="t1", input="my secret question", output="ok", context="c")
    with factory() as s:
        waiting = s.scalars(select(JobRow)).one()
    assert "secret question" not in json.dumps(waiting.payload) and waiting.payload["enc"].startswith("v1:")
    drain(factory, handlers)
    with factory() as s:
        done = s.scalars(select(JobRow)).one()
    assert done.state == "done" and done.payload == {}
    assert get_trace(factory, ws.id, "t1")[0].input_enc is None            # And the trace itself never held the text.


def test_unsampled_traces_make_no_task(setup):
    factory, ws, client, write, *_ = setup
    update_policy(factory, ws.id, sample_rate=0.0)
    send(client, write, trace_id="t1", output="x")
    assert DbQueue(factory).counts()["queued"] == 0
    assert get_trace(factory, ws.id, "t1")[0].eval_state == "not_sampled"


def test_no_evaluators_means_no_tasks_even_when_sampled(factory, key):
    ws = create_workspace(factory, "w")
    update_policy(factory, ws.id, sample_rate=1.0)
    assert ingest(factory, ws.id, [TraceIn(trace_id="t")]).eval_payloads == {}


def test_running_the_evaluation_twice_replaces_the_results(setup):
    factory, ws, client, write, admin, handlers, _ = setup
    send(client, write, trace_id="t1", output="short", context="Refunds take 30 days.", input="q")
    drain(factory, handlers)
    pk = get_trace(factory, ws.id, "t1")[0].id
    run_online_evaluation(factory, pk, {"input": "q", "output": "short", "context": "Refunds take 30 days."}, lambda n: judge_provider())
    assert len(client.get("/v1/traces/t1", headers=admin).json()["evaluations"]) == 3


def test_a_deleted_trace_is_skipped(setup):
    factory, ws, client, write, admin, handlers, _ = setup
    send(client, write, trace_id="t1", output="x")
    client.delete("/v1/traces/t1", headers=admin)
    assert drain(factory, handlers) == 1
    assert DbQueue(factory).counts()["done"] == 1


def test_a_crash_is_retried_and_the_last_failure_shows_on_the_trace(setup, monkeypatch):
    factory, ws, client, write, *_ = setup
    import trustbench.online as online
    monkeypatch.setattr(online, "run_online_evaluation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database gone")))
    handlers = default_handlers(factory)
    send(client, write, trace_id="t1", output="x")
    q = DbQueue(factory)
    for _ in range(3):
        job = q.claim("w")
        try:
            handlers[job.kind](job.payload, job)
        except RuntimeError:
            q.fail(job.id, "database gone", retry_in=0)
        if _ < 2:
            assert get_trace(factory, ws.id, "t1")[0].eval_state == "pending"       # Still waiting for the retries.
    assert get_trace(factory, ws.id, "t1")[0].eval_state == "error" and q.counts()["dead"] == 1


def test_a_judge_that_answers_garbage_is_shown_as_an_evaluator_problem_not_a_model_failure(setup):
    factory, ws, client, write, admin, _, _ = setup
    bad = FnProvider(lambda p: "garbage")
    handlers = default_handlers(factory, provider_factory=lambda name: bad)
    send(client, write, trace_id="t1", input="q", context="c", output="fine answer")
    drain(factory, handlers)
    ev = {e["evaluator"]: e for e in client.get("/v1/traces/t1", headers=admin).json()["evaluations"]}
    assert ev["llm_judge"]["details"] == {"error": True} and "Evaluator error" in ev["llm_judge"]["rationale"]


def test_rationale_is_redacted_and_details_lose_the_claim_list():
    assert sanitize_details({"claims": [{"claim": "x"}], "metrics": {"ec": 1}, "human_review": False}) == {"metrics": {"ec": 1}, "human_review": False}


def test_personal_data_in_a_rationale_is_removed(factory, key):
    ws = create_workspace(factory, "w")
    update_policy(factory, ws.id, sample_rate=1.0, online_evaluators=[{"type": "rule", "forbidden": ["ana@gmail.com"]}], redact=False)
    r = ingest(factory, ws.id, [TraceIn(trace_id="t", output="write to ana@gmail.com")])
    update_policy(factory, ws.id, redact=True)
    run_online_evaluation(factory, list(r.eval_payloads)[0], {"output": "write to ana@gmail.com"})
    from trustbench.online import trace_evaluations
    [e] = trace_evaluations(factory, ws.id, list(r.eval_payloads)[0])
    assert "ana@gmail.com" not in e.rationale and "[EMAIL]" in e.rationale
