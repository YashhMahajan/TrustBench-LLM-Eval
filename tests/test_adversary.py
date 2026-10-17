import asyncio
import json

from sqlalchemy import select

from helpers import FnProvider
from trustbench.adversary import cases_of_run, propose_from_failures
from trustbench.datasets import CaseSpec, import_dataset
from trustbench.db import ProposedCase
from trustbench.engine import RunOptions, run_eval
from trustbench.judge import Judge
from trustbench.providers import MockProvider

CTX = "The shop opens at 9 am. Parking is free for 2 hours. Dogs are welcome inside. The cafe closes at 5 pm."
CASES = [
    CaseSpec(id="g1", task="grounded_qa", input={"context": CTX, "question": "For how long is parking free? Answer briefly."},
             expected={"required_facts": ["2 hours"]}, evaluation={"evaluators": [{"type": "contains", "values": ["2 hours"]}]}),
    CaseSpec(id="ok", task="qa", input="What is the capital of France?", expected={"answer": "Paris"}),
    CaseSpec(id="f2", task="qa", input="Name the largest planet in the solar system.", expected={"answer": "Jupiter"}),
]


def make_run(factory):
    with factory() as s:
        v = import_dataset(s, "adv", CASES)
    # The mock model fails g1 and f2 and passes 'ok'.
    provider = MockProvider({"capital of France": "Paris"})
    return run_eval(factory, v.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n: provider)


def judge_writing(cases):
    return Judge(FnProvider(lambda p: json.dumps({"cases": cases})), "j")


def stored(factory):
    with factory() as s:
        return list(s.scalars(select(ProposedCase).order_by(ProposedCase.case_id)))


def test_only_failing_cases_get_transform_proposals_in_the_proposed_state(factory):
    rid = make_run(factory)
    rep = asyncio.run(propose_from_failures(factory, rid, per_case=2))
    rows = stored(factory)
    assert rep.failing_cases == 2 and rep.created == len(rows) > 0
    assert {r.source_case_id for r in rows} <= {"g1", "f2"} and "ok" not in {r.source_case_id for r in rows}
    assert all(r.status == "proposed" and r.expected_verified and r.method.startswith("transform:") for r in rows)
    g1 = [r for r in rows if r.source_case_id == "g1"]
    assert len(g1) == 2 and all(r.spec["expected"] == CASES[0].expected for r in g1)


def test_running_again_adds_nothing(factory):
    rid = make_run(factory)
    asyncio.run(propose_from_failures(factory, rid))
    n = len(stored(factory))
    rep = asyncio.run(propose_from_failures(factory, rid))
    assert rep.created == 0 and len(stored(factory)) == n and rep.skipped.get("already proposed")


def test_model_variations_are_not_verified(factory):
    rid = make_run(factory)
    judge = judge_writing([{"prompt": "Name the second largest planet in the solar system.", "answer": "Saturn"}])
    asyncio.run(propose_from_failures(factory, rid, judge=judge, per_case=1))
    adv = [r for r in stored(factory) if r.method == "model_variation"]
    assert adv and all(not r.expected_verified for r in adv)
    assert adv[0].spec["expected"] == {"required_facts": ["Saturn"]} and adv[0].spec["metadata"]["generated"] is True


def test_model_variation_that_copies_an_existing_case_is_skipped(factory):
    rid = make_run(factory)
    judge = judge_writing([{"prompt": "What is the capital of France?", "answer": "Paris"}])
    rep = asyncio.run(propose_from_failures(factory, rid, judge=judge, per_case=1))
    assert not [r for r in stored(factory) if r.method == "model_variation"]
    assert any(k.startswith("duplicate of an existing case") for k in rep.skipped)


def test_bad_model_output_and_personal_data_are_dropped(factory):
    rid = make_run(factory)
    bad = Judge(FnProvider(lambda p: "garbage"), "j")
    asyncio.run(propose_from_failures(factory, rid, judge=bad, per_case=1))
    assert not [r for r in stored(factory) if r.method == "model_variation"]
    pii = judge_writing([{"prompt": "Write to ana@gmail.com about Jupiter moons count.", "answer": "95"}])
    rep = asyncio.run(propose_from_failures(factory, rid, judge=pii, per_case=1))
    assert rep.skipped.get("possible personal data") and not [r for r in stored(factory) if r.method == "model_variation"]


def test_no_failures_means_no_proposals(factory):
    with factory() as s:
        v = import_dataset(s, "easy", [CASES[1]])
    p = MockProvider({"capital": "Paris"})
    rid = run_eval(factory, v.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n: p)
    rep = asyncio.run(propose_from_failures(factory, rid))
    assert (rep.failing_cases, rep.created, stored(factory)) == (0, 0, [])


def test_cases_of_run_returns_the_dataset(factory):
    rid = make_run(factory)
    assert {c.id for c in cases_of_run(factory, rid)} == {"g1", "ok", "f2"}
