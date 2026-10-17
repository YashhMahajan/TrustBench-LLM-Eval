import pytest
from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.datasets import CaseSpec, import_dataset
from trustbench.db import init_db, make_engine
from trustbench.engine import RunOptions, run_eval
from trustbench.providers import MockProvider

runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", url)
    factory = init_db(make_engine(url))
    cases = [CaseSpec(id=f"q{i}", task="qa", input=f"Item <{i}> please", expected={"answer": "yes"},
                      metadata={"critical": i < 3}) for i in range(60)]
    with factory() as s:
        version = import_dataset(s, "big", cases)

    def make_run(pass_n, name="mock:m"):
        # The mock answers 'yes' for the first pass_n questions.
        answers = {f"<{i}>": "yes" for i in range(pass_n)}
        return run_eval(factory, version.id, [name], RunOptions(backoff=0),
                        provider_factory=lambda n: MockProvider({**answers}))
    return tmp_path, make_run


def test_compare_shows_a_clear_regression(env):
    _, make_run = env
    good, bad = make_run(58), make_run(35)
    out = runner.invoke(app, ["compare", bad, "--baseline", good])
    assert out.exit_code == 0 and "worse" in out.output and "overall" in out.output and "P95 latency" in out.output
    same = runner.invoke(app, ["compare", good, "--baseline", good])
    assert "cannot tell" in same.output     # 60 identical cases cannot prove "no change": an unseen difference of a few points is still possible.


def test_compare_slices_and_bad_references(env):
    _, make_run = env
    good, bad = make_run(58), make_run(35)
    out = runner.invoke(app, ["compare", bad, "--baseline", good, "--slices"])
    assert out.exit_code == 0 and ("Slices that got worse" in out.output or "No slice" in out.output)
    assert runner.invoke(app, ["compare", "zzz", "--baseline", good]).exit_code == 1


def test_policy_init_and_gate_pass_and_block(env):
    tmp, make_run = env
    policy = tmp / "policy.yaml"
    assert runner.invoke(app, ["policy", "init", "--out", str(policy)]).exit_code == 0
    assert runner.invoke(app, ["policy", "init", "--out", str(policy)]).exit_code == 1   # Will not overwrite.
    policy.write_text("""
release_policy:
  name: test
  min_cases: 30
  critical_failures_allowed: 0
  overall:
    minimum_score: 0.8
    maximum_regression: 0.1
""")
    good, bad = make_run(60), make_run(30)
    base = runner.invoke(app, ["baseline", "set", "main", good])
    assert base.exit_code == 0
    ok = runner.invoke(app, ["gate", "check", good, "--policy", str(policy), "--baseline", "main"])
    assert ok.exit_code == 0 and "may go on" in ok.output
    blocked = runner.invoke(app, ["gate", "check", bad, "--policy", str(policy), "--baseline", "main"])
    assert blocked.exit_code == 1 and "blocked" in blocked.output and "fail" in blocked.output


def test_gate_without_a_baseline_blocks_regression_rules(env):
    tmp, make_run = env
    policy = tmp / "p.yaml"
    policy.write_text("overall:\n  maximum_regression: 0.01\ncritical_failures_allowed: null\nmin_cases: 10")
    out = runner.invoke(app, ["gate", "check", make_run(60), "--policy", str(policy)])
    assert out.exit_code == 1 and "inconclusive" in out.output and "no baseline" in out.output


def test_gate_errors_are_clear(env):
    tmp, make_run = env
    run = make_run(60)
    assert "policy init" in runner.invoke(app, ["gate", "check", run, "--policy", str(tmp / "none.yaml")]).output
    bad = tmp / "bad.yaml"
    bad.write_text("overall:\n  minimum_score: 7")
    assert runner.invoke(app, ["gate", "check", run, "--policy", str(bad)]).exit_code == 1


def test_baseline_commands(env):
    _, make_run = env
    run = make_run(60)
    assert runner.invoke(app, ["baseline", "set", "main", run]).exit_code == 0
    assert "main" in runner.invoke(app, ["baseline", "list"]).output
    assert runner.invoke(app, ["baseline", "set", "x", "nope"]).exit_code == 1


def test_decisions_are_saved(env):
    tmp, make_run = env
    policy = tmp / "p.yaml"
    policy.write_text("min_cases: 10\ncritical_failures_allowed: null")
    out = runner.invoke(app, ["gate", "check", make_run(60), "--policy", str(policy)])
    assert "saved as decision 1" in out.output
