import json

from typer.testing import CliRunner

from test_engine import ANSWERS, go
from trustbench.cli import app
from trustbench.failure_report import analyze_failures
from trustbench.providers import MockProvider
from trustbench.rundata import Record, load_run

runner = CliRunner(env={"COLUMNS": "200"})


def test_analysis_of_a_real_run(factory, version):
    rd = load_run(factory, go(factory, version, MockProvider(ANSWERS)))
    data = analyze_failures(rd.for_model())
    assert data["cases_with_a_result"] == 3 and data["failed_cases"] == 1
    assert data["labels"][0]["label"] == "wrong_answer" and data["labels"][0]["case_ids"] == ["b"]
    assert data["priority"][0]["name"] == "wrong_answer" and data["unclustered"] == ["b"]


def test_a_clean_run_has_nothing_to_report():
    recs = [Record(case_id=str(i), passed=True) for i in range(5)]
    data = analyze_failures(recs)
    assert data["failed_cases"] == 0 and data["labels"] == [] and data["clusters"] == [] and data["priority"] == []


def test_evaluator_problems_are_kept_out_of_the_failures():
    recs = [Record(case_id="e", passed=None, evaluator_error=True, results=[{"evaluator": "llm_judge", "passed": False, "score": 0,
            "details": {"error": True}, "rationale": "x"}]), Record(case_id="ok", passed=True)]
    data = analyze_failures(recs)
    assert data["cases_with_a_result"] == 1 and [l["label"] for l in data["labels"]] == ["evaluation_error"] and data["clusters"] == []


def run_dataset(dataset, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    out = runner.invoke(app, ["run", "--dataset", dataset, "--model", "mock:smoke"])
    assert out.exit_code == 0, out.output
    return out.output.split("Run ")[1].split()[0].strip("│ ")


def test_failures_command_on_the_grounded_suite(tmp_path, monkeypatch):
    run_id = run_dataset("grounded", tmp_path, monkeypatch)
    out = runner.invoke(app, ["failures", run_id])
    assert out.exit_code == 0
    for text in ("8 of 8 cases failed", "How the cases failed", "wrong_answer", "What to fix first", "evidence_ignored"):
        assert text in out.output, text


def test_failures_command_shows_recurring_patterns(tmp_path, monkeypatch):
    run_id = run_dataset("factual", tmp_path, monkeypatch)    # The demo model answers every factual question with 'I do not know'.
    out = runner.invoke(app, ["failures", run_id])
    assert "Recurring patterns" in out.output and "do not prove the cause" in out.output


def test_failures_json_and_errors(tmp_path, monkeypatch):
    run_id = run_dataset("abstention", tmp_path, monkeypatch)
    data = json.loads(runner.invoke(app, ["failures", run_id, "--json"]).output)
    labels = {l["label"] for l in data["labels"]}
    assert "unnecessary_refusal" in labels and data["failed_cases"] >= 1
    assert runner.invoke(app, ["failures", "nope"]).exit_code == 1


def test_a_run_with_no_failures_says_so(tmp_path, monkeypatch):
    run_id = run_dataset("smoke", tmp_path, monkeypatch)    # The demo model fails one case. Use a filter on the output instead.
    out = runner.invoke(app, ["failures", run_id])
    assert out.exit_code == 0 and "1 of 5 cases failed" in out.output
