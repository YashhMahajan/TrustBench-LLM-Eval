import asyncio

import pytest
from typer.testing import CliRunner

from test_adversary import CASES, judge_writing, make_run, stored
from trustbench import proposals
from trustbench.adversary import propose_from_failures
from trustbench.cli import app
from trustbench.datasets import load_cases
from trustbench.db import init_db, make_engine
from trustbench.proposals import ReviewError


@pytest.fixture
def pool(factory):
    rid = make_run(factory)
    judge = judge_writing([{"prompt": "Name the second largest planet in the solar system.", "answer": "Saturn"}])
    asyncio.run(propose_from_failures(factory, rid, judge=judge, per_case=1))
    return factory


def ids(rows, verified):
    return [p.id for p in rows if p.expected_verified == verified]


def test_verified_proposals_can_be_approved_directly(pool):
    rows = stored(pool)
    assert proposals.approve(pool, ids(rows, True)[:1]) == 1
    assert [p.status for p in stored(pool) if p.id == ids(rows, True)[0]] == ["approved"]


def test_unverified_answers_need_a_person_to_confirm(pool):
    bad = ids(stored(pool), False)
    with pytest.raises(ReviewError, match="answer written by a model"):
        proposals.approve(pool, bad)
    assert all(p.status == "proposed" for p in stored(pool))     # Nothing changed.
    assert proposals.approve(pool, bad, confirm_unverified=True) == len(bad)


def test_one_bad_id_approves_nothing(pool):
    good = ids(stored(pool), True)[0]
    with pytest.raises(ReviewError, match="No proposal 999"):
        proposals.approve(pool, [good, 999])
    assert all(p.status == "proposed" for p in stored(pool))


def test_editing_the_answer_marks_it_as_checked(pool):
    pid = ids(stored(pool), False)[0]
    proposals.edit_answer(pool, pid, "Saturn (95 known moons)")
    p = next(x for x in stored(pool) if x.id == pid)
    assert p.expected_verified and p.spec["expected"] == {"required_facts": ["Saturn (95 known moons)"]}
    assert proposals.approve(pool, [pid]) == 1


def test_rejected_cases_stay_out_and_are_not_proposed_again(pool, tmp_path):
    rid = stored(pool)[0].source_run_id
    pid = stored(pool)[0].id
    proposals.reject(pool, [pid], note="not useful")
    n = len(stored(pool))
    asyncio.run(propose_from_failures(pool, rid, per_case=1))
    assert len(stored(pool)) == n and proposals.list_proposals(pool, "rejected")[0].note == "not useful"
    with pytest.raises(ReviewError, match="No approved"):
        proposals.export_approved(pool, "adv", tmp_path / "none.jsonl")


def test_export_writes_base_plus_approved_and_versions_it(pool, tmp_path):
    rows = stored(pool)
    proposals.approve(pool, ids(rows, True)[:1])
    out = tmp_path / "r.jsonl"
    n, v1 = proposals.export_approved(pool, "adv", out, base=CASES)
    assert n == 1 and len(load_cases(out)) == len(CASES) + 1
    proposals.approve(pool, ids(rows, True)[1:2])
    n2, v2 = proposals.export_approved(pool, "adv", out, base=CASES)
    assert n2 == 2 and v2 != v1                      # More approvals give a new dataset version.
    assert proposals.export_approved(pool, "adv", out, base=CASES)[1] == v2   # The same content gives the same version.


def test_cli_review_loop(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    factory = init_db(make_engine(url))
    rid = make_run(factory)
    runner = CliRunner(env={"COLUMNS": "200"})
    gen = runner.invoke(app, ["proposals", "generate", rid, "--per-case", "1"])
    assert gen.exit_code == 0 and "proposal(s) created" in gen.output
    listing = runner.invoke(app, ["proposals", "list"])
    assert "transform:" in listing.output and "g1" in listing.output
    first = stored(factory)[0].id
    assert runner.invoke(app, ["proposals", "show", str(first)]).exit_code == 0
    assert runner.invoke(app, ["proposals", "approve", str(first)]).exit_code == 0
    out = tmp_path / "reg.jsonl"
    exp = runner.invoke(app, ["proposals", "export", "adv", "--only-new", "--out", str(out)])
    assert exp.exit_code == 0 and len(load_cases(out)) == 1
    assert runner.invoke(app, ["proposals", "approve", "999"]).exit_code == 1
    assert runner.invoke(app, ["proposals", "show", "999"]).exit_code == 1
