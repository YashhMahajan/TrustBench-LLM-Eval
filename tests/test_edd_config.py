"""trustbench.yaml: loading, checking, and the `init` and `run` commands that use it."""

import json

import pytest
from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.edd.config import TEMPLATE, ConfigError, find_config, load_config, parse_config

runner = CliRunner(env={"COLUMNS": "200"})


def test_the_template_is_valid_and_says_what_it_means():
    cfg = parse_config(TEMPLATE)
    assert cfg.dataset == "smoke" and cfg.specs() == ["mock:smoke"] and cfg.run.reps == 1 and cfg.run.cache is True


def test_defaults_when_the_file_is_nearly_empty():
    cfg = parse_config("version: 1\n")
    assert cfg.targets == {} and cfg.policy == "policy.yaml" and cfg.run.concurrency == 4 and cfg.judge is None


@pytest.mark.parametrize("text,needle", [
    ("targets: {a: nocolon}", "must look like provider:model"),
    ("targets: {a: 'mock:x'}\ndefault_targets: [b]", "unknown targets"),
    ("version: 2", "version must be 1"),
    ("run: {reps: 0}", "run.reps"),
    ("run: {budget: -1}", "run.budget"),
    ("surprise: 1", "surprise"),
    ("run: {reps: 1, turbo: true}", "turbo"),
    ("- a\n- b", "must be a YAML mapping"),
    ("a: [", "not valid YAML"),
])
def test_a_bad_file_gives_a_clear_error(text, needle):
    with pytest.raises(ConfigError, match=needle):
        parse_config(text)


def test_specs_pick_default_targets_then_all_then_named():
    cfg = parse_config("targets: {a: 'mock:a', b: 'mock:b', c: 'mock:c'}\ndefault_targets: [b]")
    assert cfg.specs() == ["mock:b"] and cfg.specs(["c", "a"]) == ["mock:c", "mock:a"]
    assert parse_config("targets: {a: 'mock:a', b: 'mock:b'}").specs() == ["mock:a", "mock:b"]
    with pytest.raises(ConfigError, match="Unknown target"):
        cfg.specs(["zzz"])


def test_find_config_uses_the_named_file_or_the_default_in_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert find_config() is None and load_config() is None
    (tmp_path / "trustbench.yaml").write_text("version: 1\n")
    assert find_config().name == "trustbench.yaml" and load_config().version == 1
    with pytest.raises(ConfigError, match="not found"):
        find_config(tmp_path / "nope.yaml")


# ---- Commands ---------------------------------------------------------------------------------------

@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    return tmp_path


def test_init_writes_a_file_and_refuses_to_replace_it(work):
    assert runner.invoke(app, ["init"]).exit_code == 0 and (work / "trustbench.yaml").read_text() == TEMPLATE
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 1 and "exists" in r.output
    (work / "trustbench.yaml").write_text("changed")
    assert runner.invoke(app, ["init", "--force"]).exit_code == 0 and (work / "trustbench.yaml").read_text() == TEMPLATE


def test_run_with_no_flags_uses_the_file(work):
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["run"])
    assert r.exit_code == 0, r.output
    assert "mock:smoke" in r.output and "Dataset smoke" in r.output


def test_flags_win_over_the_file(work):
    (work / "trustbench.yaml").write_text("dataset: factual\ntargets: {a: 'mock:smoke'}\nrun: {reps: 3}\n")
    r = runner.invoke(app, ["run", "--dataset", "smoke", "--model", "mock:other", "--reps", "1"])
    assert r.exit_code == 0 and "Dataset smoke" in r.output and "mock:other" in r.output and "mock:smoke" not in r.output


def test_the_file_sets_run_options_and_a_target_name_picks_a_target(work):
    (work / "trustbench.yaml").write_text("dataset: smoke\ntargets: {a: 'mock:a', b: 'mock:b'}\ndefault_targets: [a]\nrun: {reps: 2}\n")
    r = runner.invoke(app, ["run", "--target", "b"])
    assert r.exit_code == 0 and "mock:b" in r.output and "mock:a" not in r.output
    from trustbench.cli_common import get_factory
    from trustbench.db import TestExecution
    with get_factory()() as s:
        assert s.query(TestExecution).count() == 10          # 5 smoke cases x 2 repetitions


def test_a_bad_config_or_an_unknown_target_is_a_clean_error_not_a_traceback(work):
    (work / "trustbench.yaml").write_text("surprise: 1")
    r = runner.invoke(app, ["run"])
    assert r.exit_code == 1 and "surprise" in r.output
    (work / "trustbench.yaml").write_text("targets: {a: 'mock:a'}")
    r = runner.invoke(app, ["run", "--target", "zzz"])
    assert r.exit_code == 1 and "Unknown target" in r.output
    (work / "trustbench.yaml").unlink()
    r = runner.invoke(app, ["run", "--target", "a"])
    assert r.exit_code == 1 and "needs a trustbench.yaml" in r.output
    r = runner.invoke(app, ["run", "--config", "missing.yaml"])
    assert r.exit_code == 1 and "not found" in r.output


def test_a_python_target_from_the_file_runs(work, monkeypatch):
    (work / "my_app.py").write_text("def answer(prompt): return 'Paris'\n")
    monkeypatch.syspath_prepend(str(work))
    (work / "d.jsonl").write_text(json.dumps({"id": "a", "input": "Capital of France?", "expected": {"answer": "Paris"}}) + "\n")
    (work / "trustbench.yaml").write_text("dataset: d.jsonl\ntargets: {v1: 'python:my_app:answer@v1'}\n")
    r = runner.invoke(app, ["run"])
    assert r.exit_code == 0 and "100%" in r.output, r.output
