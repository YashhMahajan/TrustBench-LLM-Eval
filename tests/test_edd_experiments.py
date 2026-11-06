"""Experiments: a history of variants, and a verdict on each hypothesis."""

import json

import pytest
from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.comparison import Comparison, Delta
from trustbench.datasets import CaseSpec, import_dataset
from trustbench.db import Experiment
from trustbench.edd import experiments
from trustbench.edd.experiments import ExperimentError, compare_with_parent, hypothesis_verdict, prompt_diff, record, timeline
from trustbench.engine import RunOptions, run_eval
from trustbench.providers import MockProvider
from trustbench.slices import SliceDelta

runner = CliRunner(env={"COLUMNS": "200"})
N = 60


@pytest.fixture
def dataset(factory):
    """60 cases in two tasks: 'refund' and 'shipping'. The answer to case i is 'A<i>'."""
    cases = [CaseSpec(id=f"c{i}", task="refund" if i < 30 else "shipping", input=f"Q{i}?", expected={"answer": f"A{i}"}) for i in range(N)]
    with factory() as s:
        return import_dataset(s, "exp", cases)


def make_run(factory, dataset, right, system_prompt=None):
    """A run where X says whether the app answers case i correctly."""
    answers = {f"Q{i}?": (f"A{i}" if right(i) else "wrong") for i in range(N)}
    provider = MockProvider(answers)
    return run_eval(factory, dataset.id, ["mock:m"], RunOptions(backoff=0, system_prompt=system_prompt), provider_factory=lambda n: provider)


# ---- Recording --------------------------------------------------------------------------------------

def test_a_run_can_be_named_with_a_parent_and_a_hypothesis(factory, dataset):
    r0, r1 = make_run(factory, dataset, lambda i: i % 2 == 0), make_run(factory, dataset, lambda i: True)
    v0 = record(factory, "v0", r0)
    v1 = record(factory, "v1", r1, parent="v0", hypothesis="Schema tools fix bad arguments")
    assert v0.parent_id is None and v1.parent_id == v0.id and v1.model == "mock:m"
    rows = timeline(factory)
    assert [(r.name, r.parent) for r in rows] == [("v0", None), ("v1", "v0")]
    assert rows[1].score.rate == 1.0 and rows[0].score.rate == 0.5 and rows[1].hypothesis.startswith("Schema")


def test_names_are_history_and_are_not_reused(factory, dataset):
    r = make_run(factory, dataset, lambda i: True)
    record(factory, "v0", r)
    with pytest.raises(ExperimentError, match="exists"):
        record(factory, "v0", r)


def test_a_run_with_several_models_needs_a_choice_when_it_is_recorded(factory, dataset):
    p = MockProvider({f"Q{i}?": f"A{i}" for i in range(N)})
    both = run_eval(factory, dataset.id, ["mock:a", "mock:b"], RunOptions(backoff=0), provider_factory=lambda n: p)
    with pytest.raises(ValueError, match="several models"):
        record(factory, "v0", both)
    assert record(factory, "v0", both, model="mock:b").model == "mock:b"


def test_bad_references_are_clear_errors(factory, dataset):
    r = make_run(factory, dataset, lambda i: True)
    with pytest.raises(ExperimentError, match="No experiment named 'ghost'"):
        record(factory, "v1", r, parent="ghost")
    with pytest.raises(ValueError, match="not a baseline name or a run id"):
        record(factory, "v1", "no-such-run")
    with pytest.raises(ExperimentError, match="needs a name"):
        record(factory, "  ", r)
    with factory() as s:
        assert s.query(Experiment).count() == 0                  # A failed record leaves nothing behind.


def test_workspaces_do_not_share_names(factory, dataset):
    r = make_run(factory, dataset, lambda i: True)
    record(factory, "v0", r, workspace_id="ws1")
    record(factory, "v0", r, workspace_id="ws2")
    record(factory, "v0", r)
    assert [x.name for x in timeline(factory, "ws1")] == ["v0"] and len(timeline(factory)) == 1
    with pytest.raises(ExperimentError, match="No experiment"):
        record(factory, "v1", r, parent="v0", workspace_id="ws3")


# ---- Verdicts ---------------------------------------------------------------------------------------

def delta(verdict):
    return Delta("overall", 100, 0.5, 0.6, 0.1, (0.0, 0.2), 0.05, "mcnemar", verdict)


def slice_regression():
    return SliceDelta("task", "shipping", 30, 0.9, 0.5, -0.4, 0.001, True, False)


@pytest.mark.parametrize("verdict,slices,expected", [
    ("meaningful_improvement", [], "supported"), ("improvement_below_threshold", [], "supported"),
    ("meaningful_improvement", [slice_regression()], "mixed"),
    ("meaningful_regression", [], "refuted"), ("regression_below_threshold", [], "refuted"),
    ("meaningful_regression", [slice_regression()], "refuted"),
    ("equivalent", [], "no_effect"), ("inconclusive", [], "cannot_tell"),
])
def test_the_verdict_reads_the_overall_change_and_the_slices_together(verdict, slices, expected):
    assert hypothesis_verdict(Comparison(delta(verdict), {}, 100, 0), slices) == expected


def test_an_improvement_is_supported(factory, dataset):
    record(factory, "v0", make_run(factory, dataset, lambda i: i % 2 == 0))
    record(factory, "v1", make_run(factory, dataset, lambda i: True), parent="v0", hypothesis="h")
    rep = compare_with_parent(factory, "v1")
    assert rep.verdict == "supported" and rep.parent == "v0" and rep.hypothesis == "h" and not rep.regressed_slices
    assert rep.comparison.overall.diff == pytest.approx(0.5) and rep.cost is None and rep.prompt_diff == ""


def test_a_gain_that_breaks_a_slice_is_mixed(factory, dataset):
    """The refund task goes from 0 to 30 passes (a big overall gain). Eight shipping cases that passed now fail."""
    record(factory, "v0", make_run(factory, dataset, lambda i: i >= 30))
    record(factory, "v1", make_run(factory, dataset, lambda i: i < 30 or i >= 38), parent="v0")
    rep = compare_with_parent(factory, "v1")
    assert rep.comparison.overall.verdict == "meaningful_improvement"
    assert [(x.dimension, x.value) for x in rep.regressed_slices] == [("task", "shipping")]
    assert rep.verdict == "mixed"


def test_a_regression_is_refuted(factory, dataset):
    record(factory, "v0", make_run(factory, dataset, lambda i: True))
    record(factory, "v1", make_run(factory, dataset, lambda i: i % 2 == 0), parent="v0")
    assert compare_with_parent(factory, "v1").verdict == "refuted"


def test_no_change_is_no_effect_only_when_the_interval_is_inside_the_threshold(factory, dataset):
    record(factory, "v0", make_run(factory, dataset, lambda i: True))
    record(factory, "v1", make_run(factory, dataset, lambda i: True), parent="v0")
    assert compare_with_parent(factory, "v1", min_effect=0.1).verdict == "no_effect"
    # 60 identical pairs cannot rule out a change of a few points, so a 1-point threshold honestly says "cannot tell".
    assert compare_with_parent(factory, "v1", min_effect=0.01).verdict == "cannot_tell"


def test_comparing_needs_a_parent(factory, dataset):
    record(factory, "v0", make_run(factory, dataset, lambda i: True))
    with pytest.raises(ExperimentError, match="no parent"):
        compare_with_parent(factory, "v0")


def test_the_report_includes_dollars_when_there_is_a_cost_model(factory, dataset):
    from trustbench.edd.costmodel import CostModel
    cases = [CaseSpec(id=f"k{i}", input=f"Q{i}?", expected={"answer": f"A{i}"}, metadata={"positive": i % 2 == 0}) for i in range(N)]
    with factory() as s:
        labeled = import_dataset(s, "labeled", cases)

    def run(right):
        p = MockProvider({f"Q{i}?": (f"A{i}" if right(i) else "x") for i in range(N)})
        return run_eval(factory, labeled.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n: p)
    record(factory, "v0", run(lambda i: i % 3 != 0))
    record(factory, "v1", run(lambda i: True), parent="v0")
    cm = CostModel(volume_per_year=1000, positive_rate=0.5, costs=dict(false_negative=30, false_positive=2), processing_cost_per_item=0.0)
    rep = compare_with_parent(factory, "v1", cost_model=cm)
    parent, child = rep.cost
    assert child.annual_cost < parent.annual_cost and child.fn_rate == 0.0


# ---- Prompts ----------------------------------------------------------------------------------------

def test_a_changed_system_prompt_is_shown_as_a_diff_and_an_unchanged_one_is_not(factory, dataset):
    a = make_run(factory, dataset, lambda i: True, system_prompt="You are helpful.\nBe brief.")
    b = make_run(factory, dataset, lambda i: True, system_prompt="You are helpful.\nBe brief and cite sources.")
    c = make_run(factory, dataset, lambda i: True, system_prompt="You are helpful.\nBe brief.")
    d = prompt_diff(factory, a, b)
    assert "-Be brief." in d and "+Be brief and cite sources." in d and f"{a} (parent)" in d
    assert prompt_diff(factory, a, c) == ""
    assert "Be brief" in prompt_diff(factory, make_run(factory, dataset, lambda i: True), a)      # From no prompt to a prompt.


# ---- Commands ---------------------------------------------------------------------------------------

@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/e.db")
    cases = [{"id": f"c{i}", "input": "What is the capital of France?" if i % 2 else "Is this a leap year? 2023",
              "expected": {"answer": "Paris" if i % 2 else "no"}} for i in range(40)]
    (tmp_path / "d.jsonl").write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    (tmp_path / "p1.txt").write_text("Answer in one word.")
    (tmp_path / "p2.txt").write_text("Answer in one word. Do not explain.")
    return tmp_path


def run_ids():
    from trustbench.cli_common import get_factory
    from trustbench.db import EvaluationRun
    with get_factory()() as s:
        return [r.id for r in s.query(EvaluationRun).order_by(EvaluationRun.created_at)]


def test_the_commands_tell_the_whole_story(work):
    assert "No experiments yet" in runner.invoke(app, ["experiment", "list"]).output
    for prompt in ("p1.txt", "p2.txt"):
        assert runner.invoke(app, ["run", "--dataset", "d.jsonl", "--model", "mock:smoke", "--system-prompt-file", prompt]).exit_code == 0
    r0, r1 = run_ids()
    assert runner.invoke(app, ["experiment", "record", "v0", r0]).exit_code == 0
    r = runner.invoke(app, ["experiment", "record", "v1", r1, "--parent", "v0", "--hypothesis", "Shorter answers pass exact match"])
    assert r.exit_code == 0 and "changes v0" in r.output
    out = runner.invoke(app, ["experiment", "list"]).output
    assert "v0" in out and "v1" in out and "Shorter answers" in out
    cmp_out = runner.invoke(app, ["experiment", "compare", "v1"])
    assert cmp_out.exit_code == 0, cmp_out.output
    assert "against its parent" in cmp_out.output and "Prompt change" in cmp_out.output and "+Answer in one word. Do not explain." in cmp_out.output
    assert "no real effect" in cmp_out.output                       # The mock answers the same either way.


def test_command_errors_are_clean(work):
    assert runner.invoke(app, ["run", "--dataset", "d.jsonl", "--model", "mock:smoke"]).exit_code == 0
    r0 = run_ids()[0]
    runner.invoke(app, ["experiment", "record", "v0", r0])
    again = runner.invoke(app, ["experiment", "record", "v0", r0])
    assert again.exit_code == 1 and "exists" in again.output
    assert runner.invoke(app, ["experiment", "compare", "v0"]).exit_code == 1
    assert runner.invoke(app, ["experiment", "compare", "ghost"]).exit_code == 1
    assert runner.invoke(app, ["run", "--dataset", "d.jsonl", "--model", "mock:smoke", "--system-prompt-file", "missing.txt"]).exit_code == 1


def test_the_system_prompt_file_can_come_from_the_config_file(work):
    (work / "trustbench.yaml").write_text("dataset: d.jsonl\ntargets: {a: 'mock:smoke'}\nrun: {system_prompt_file: p1.txt}\n")
    assert runner.invoke(app, ["run"]).exit_code == 0
    from trustbench.cli_common import get_factory
    from trustbench.db import EvaluationRun
    with get_factory()() as s:
        assert s.query(EvaluationRun).one().config_snapshot["options"]["system_prompt"] == "Answer in one word."


def test_the_module_exports_what_the_commands_use():
    assert {"record", "timeline", "compare_with_parent", "prompt_diff", "hypothesis_verdict"} <= set(dir(experiments))
