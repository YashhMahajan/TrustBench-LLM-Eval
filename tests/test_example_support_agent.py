"""The flagship example must keep telling the truth: each version scores what the story says it scores."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trustbench import report
from trustbench.cli import app
from trustbench.cli_common import get_factory
from trustbench.datasets import load_cases
from trustbench.failures import collect_failures
from trustbench.rundata import load_run

EX = Path(__file__).resolve().parent.parent / "examples" / "support_agent"
runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def example(tmp_path, monkeypatch):
    monkeypatch.chdir(EX)
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/ex.db")
    return tmp_path


def run_version(target, *extra):
    r = runner.invoke(app, ["run", "--target", target, *extra])
    assert r.exit_code == 0, r.output
    from trustbench.db import EvaluationRun
    with get_factory()() as s:
        run_id = s.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).first().id
    return run_id


def passed(run_id):
    rows = report.case_rows(get_factory(), run_id)
    return sum(1 for r in rows if r["passed"]), len(rows)


def test_each_version_scores_what_the_story_says(example):
    scores = {v: passed(run_version(v)) for v in ("v0", "v1", "v2", "v3")}
    assert scores == {"v0": (3, 48), "v1": (29, 48), "v2": (48, 48), "v3": (48, 48)}


def test_the_failures_of_each_version_have_the_labels_that_say_how_it_failed(example):
    def labels(target):
        data = load_run(get_factory(), run_version(target))
        return {f.case_id: set(f.labels) for f in collect_failures(data.records)}
    v0, v1 = labels("v0"), labels("v1")
    assert {"bad_args", "wrong_tool", "gave_up", "task_not_completed"} <= set().union(*v0.values())
    assert "looped" in v1["edge-unknown-1"] and "looped" in v1["edge-unknown-2"]       # v1 retries an unknown order forever.
    assert "wrong_tool" in v1["refund-over-limit-01"]                                   # v1 refunds when it should escalate.
    assert "refund-01" not in v1 and "status-01" not in v1                              # And the fixes it did make hold.


def test_v1_is_better_than_v0_but_the_gate_blocks_it_and_v2_passes(example):
    r0, r1, r2 = run_version("v0"), run_version("v1"), run_version("v2")
    blocked = runner.invoke(app, ["gate", "check", r1, "--baseline", r0])
    assert blocked.exit_code == 1
    from trustbench.baselines import list_decisions
    verdict = {c["rule"]: c["status"] for c in list_decisions(get_factory(), r1)[0].checks}
    assert verdict["critical_failures"] == "fail" and verdict["annual_cost"] == "fail" and verdict["overall.minimum_score"] == "fail"
    assert verdict["overall.maximum_regression"] == "pass"                               # Better than v0, and still not shippable.
    assert runner.invoke(app, ["gate", "check", r2, "--baseline", r1]).exit_code == 0
    runner.invoke(app, ["experiment", "record", "v0", r0])
    runner.invoke(app, ["experiment", "record", "v1", r1, "--parent", "v0"])
    out = runner.invoke(app, ["experiment", "compare", "v1"]).output
    assert "Hypothesis: supported" in out                                                # Better, yet not shippable.


def test_the_committed_dataset_is_what_the_builder_writes():
    path = EX / "datasets" / "support.jsonl"
    before = path.read_text()
    subprocess.run([sys.executable, str(EX / "build_dataset.py")], cwd=EX, check=True, capture_output=True)
    assert path.read_text() == before, "Run `python examples/support_agent/build_dataset.py` and commit the result."


def test_the_dataset_has_the_slices_critical_cases_and_labels_the_docs_describe():
    cases = load_cases(EX / "datasets" / "support.jsonl")
    tasks = {}
    for c in cases:
        tasks[c.task] = tasks.get(c.task, 0) + 1
    assert len(cases) == 48 and tasks == {"status": 10, "refund": 14, "policy": 8, "safety": 6, "multi_turn": 8, "edge": 2}
    assert sum(c.metadata["critical"] for c in cases) == 14 and sum(c.metadata["positive"] for c in cases) == 14
    assert all("messages" in c.input for c in cases)


def test_the_whole_demo_runs_with_no_key_and_ends_the_way_the_story_says():
    r = subprocess.run([sys.executable, str(EX / "demo.py")], cwd=EX, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    decisions = [line for line in r.stdout.splitlines() if line.startswith("Decision:")]
    assert [d.split(".")[0] for d in decisions] == ["Decision: fail", "Decision: pass", "Decision: pass"]
    assert "Hypothesis: supported" in r.stdout and "t-damaged-001" in r.stdout and "49 cases" in r.stdout
    assert "v3" in r.stdout.split("The whole history")[1]


def test_the_example_policy_and_config_are_valid():
    from trustbench.edd.config import parse_config
    from trustbench.policy import load_policy
    cfg = parse_config((EX / "trustbench.yaml").read_text())
    assert cfg.specs(["v0", "v1", "v2", "v3"])[0].startswith("python:support_agent.v0") and cfg.dataset == "datasets/support.jsonl"
    pol = load_policy(EX / "policy.yaml")
    assert pol.cost_model.volume_per_year == 500_000 and pol.annual_cost_max_usd == 900_000 and pol.critical_failures_allowed == 0
    json.loads(json.dumps(pol.model_dump()))
