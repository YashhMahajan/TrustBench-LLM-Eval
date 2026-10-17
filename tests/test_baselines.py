import pytest

from test_engine import ANSWERS, go
from trustbench.baselines import list_baselines, list_decisions, resolve, save_decision, set_baseline
from trustbench.gate import evaluate_gate
from trustbench.policy import ReleasePolicy, Rule
from trustbench.providers import MockProvider
from trustbench.rundata import load_run


def test_baseline_set_resolve_and_move(factory, version):
    r1 = go(factory, version, MockProvider(ANSWERS))
    r2 = go(factory, version, MockProvider(ANSWERS))
    set_baseline(factory, "main", r1, "mock:m")
    assert resolve(factory, "main") == (r1, "mock:m")
    set_baseline(factory, "main", r2)
    assert resolve(factory, "main") == (r2, None)
    assert [b.name for b in list_baselines(factory)] == ["main"]


def test_run_id_resolves_to_itself_and_unknown_names_fail(factory, version):
    r1 = go(factory, version, MockProvider(ANSWERS))
    assert resolve(factory, r1) == (r1, None)
    with pytest.raises(ValueError, match="not a baseline name"):
        resolve(factory, "nope")
    with pytest.raises(ValueError, match="No run"):
        set_baseline(factory, "x", "nope")


def test_decision_keeps_policy_and_evidence(factory, version):
    r1 = go(factory, version, MockProvider(ANSWERS))
    policy = ReleasePolicy(name="prod", min_cases=2, critical_failures_allowed=None, overall=Rule(minimum_score=0.5))
    decision = evaluate_gate(policy, load_run(factory, r1))
    did = save_decision(factory, r1, "mock:m", (r1, "mock:m"), policy, decision)
    saved = list_decisions(factory, r1)[0]
    assert saved.id == did and saved.status == decision.status and saved.policy_name == "prod"
    assert saved.policy_snapshot["overall"]["minimum_score"] == 0.5
    assert saved.checks[0]["rule"] == "evaluation_complete" and saved.baseline_run_id == r1
    assert list_decisions(factory, "other") == []
