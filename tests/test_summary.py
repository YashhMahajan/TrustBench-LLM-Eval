import json

from typer.testing import CliRunner

from test_engine import ANSWERS, go
from trustbench.cli import app
from trustbench.providers import MockProvider
from trustbench.rundata import Record, load_run
from trustbench.summary import hallucination_from_records, run_summary


def claim_result(sup, insuff, contra=0, na=0, review=0):
    total = sup + insuff + contra + na
    return {"evaluator": "grounded_claims", "passed": True, "score": 1.0,
            "details": {"metrics": {"total": total, "supported": sup, "contradicted": contra, "insufficient": insuff,
                                    "not_applicable": na, "needs_review": review}}}


def test_hallucination_metrics_are_pooled_over_responses():
    recs = [Record(results=[claim_result(9, 1)]), Record(results=[claim_result(0, 0, na=2)]), Record(results=[])]
    h = hallucination_from_records(recs)
    assert (h.total, h.supported, h.insufficient, h.not_applicable) == (12, 9, 1, 2)
    assert h.evidence_coverage == 0.9 and h.unassessed_rate == 2 / 12
    assert hallucination_from_records([Record(results=[])]) is None


def test_run_summary_is_json_ready(factory, version):
    rd = load_run(factory, go(factory, version, MockProvider(ANSWERS), specs=("mock:m1", "mock:m2")))
    data = json.loads(json.dumps(run_summary(rd), default=str))
    assert set(data["models"]) == {"mock:m1", "mock:m2"} and data["status"] == "complete"
    m = data["models"]["mock:m1"]
    assert m["overall"]["n"] == 3 and m["operations"]["calls"] == 3 and "hallucination" not in m


def test_report_command_tables_and_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    runner = CliRunner(env={"COLUMNS": "200"})
    out = runner.invoke(app, ["run", "--model", "mock:smoke"])
    run_id = out.output.split("Run ")[1].split()[0].strip("│ ")
    text = runner.invoke(app, ["report", run_id])
    assert text.exit_code == 0 and "95% interval" in text.output and "Latency P50" in text.output
    js = runner.invoke(app, ["report", run_id, "--json"])
    assert json.loads(js.output)["models"]["mock:smoke"]["overall"]["n"] == 5
    assert runner.invoke(app, ["report", "nope"]).exit_code == 1
    assert runner.invoke(app, ["report", run_id, "--model", "x:y"]).exit_code == 1
