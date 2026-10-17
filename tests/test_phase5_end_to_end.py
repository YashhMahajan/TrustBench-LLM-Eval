"""The whole production loop in one test: SDK -> API -> worker -> monitor -> test case -> dataset -> gate."""

import json

from fastapi.testclient import TestClient

from helpers import FnProvider
from trustbench import crypto, proposals, report
from trustbench.api import create_app
from trustbench.baselines import save_decision
from trustbench.dataprotect import update_policy
from trustbench.datasets import import_dataset, load_cases
from trustbench.engine import RunOptions, run_eval
from trustbench.gate import evaluate_gate
from trustbench.handlers import default_handlers
from trustbench.jobs import DbQueue, run_worker
from trustbench.monitor import monitor_summary
from trustbench.policy import ReleasePolicy, Rule
from trustbench.production_tests import flagged_traces, promote
from trustbench.providers import MockProvider
from trustbench.rundata import load_run
from trustbench.sdk import TrustBench
from trustbench.tenancy import create_api_key, create_workspace


def test_a_production_failure_becomes_a_test_that_guards_the_next_release(factory, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    ws = create_workspace(factory, "acme")
    other = create_workspace(factory, "rival")
    update_policy(factory, ws.id, store_content=True, sample_rate=1.0, online_evaluators=[{"type": "rule", "max_words": 6, "forbidden": ["sorry"]}])
    _, write = create_api_key(factory, ws.id, "app", "write")
    _, rival_read = create_api_key(factory, other.id, "spy", "read")
    server = TestClient(create_app(factory))

    # 1. The application sends traces with the SDK. One answer breaks the rules of the team.
    tb = TrustBench(api_key=write, base_url="http://testserver", client=server, flush_interval=3600)
    for i in range(20):
        with tb.trace("support-bot", model="m") as t:
            t.input = f"Where is order {1000 + i}? Write to ana@gmail.com"
            t.output = "Shipped yesterday." if i != 7 else "Sorry, I am very sorry, I do not know where it is."
    tb.flush()
    assert tb.stats.sent == 20

    # 2. Workers check the sampled traces.
    handlers = default_handlers(factory)
    assert run_worker(DbQueue(factory), handlers, concurrency=1, idle_exit=True, poll_interval=0.01) == 20
    assert monitor_summary(factory, ws.id, 1)["measured"]["check:rule"]["pass_rate"]["k"] == 19

    # 3. The failure is flagged. Personal data was removed before anything was stored.
    [flag] = flagged_traces(factory, ws.id)
    assert flag.reasons == ["check:rule (measured)"]
    p = promote(factory, ws.id, flag.trace_id)
    assert "gmail" not in json.dumps(p.spec) and "[EMAIL]" in json.dumps(p.spec)

    # 4. Another workspace sees nothing of this.
    assert server.get("/v1/traces", headers={"Authorization": f"Bearer {rival_read}"}).json()["traces"] == []
    assert server.get(f"/v1/traces/{flag.trace_id}", headers={"Authorization": f"Bearer {rival_read}"}).status_code == 404

    # 5. A person approves it. The test joins a dataset.
    proposals.approve(factory, [p.id])
    path = tmp_path / "regression.jsonl"
    proposals.export_approved(factory, "support-bot-production", path)
    with factory() as s:
        version = import_dataset(s, "regression", load_cases(path))

    # 6. The next release is tested against the old failure. The gate blocks the old behaviour and allows the fix.
    policy = ReleasePolicy(min_cases=1, critical_failures_allowed=None, overall=Rule(minimum_score=0.0))
    bad, good = MockProvider({"Where is order": "Sorry, I am very sorry, I do not know where it is."}), MockProvider({"Where is order": "Shipped yesterday."})
    outcomes = {}
    for label, provider in (("bad", bad), ("good", good)):
        rid = run_eval(factory, version.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n, p=provider: p)
        outcomes[label] = report.case_rows(factory, rid)[0]["passed"]
    assert outcomes == {"bad": False, "good": True}
