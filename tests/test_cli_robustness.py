import json

from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.datasets import CaseSpec, load_cases
from trustbench.quality import check

runner = CliRunner(env={"COLUMNS": "200"})


def test_variants_do_not_raise_duplicate_warnings():
    a = CaseSpec(id="a", input="What is the refund period for the shop?", expected={"answer": "30 days"})
    b = a.model_copy(update={"id": "a~whitespace", "metadata": {"source_case": "a", "transform": "whitespace"}})
    c = a.model_copy(update={"id": "z", "metadata": {}})
    assert check([a, b]) == []
    assert [i.code for i in check([a, c])] == ["duplicate"]     # Real duplicates are still found.


def test_build_writes_a_runnable_dataset(tmp_path):
    out = tmp_path / "g.jsonl"
    res = runner.invoke(app, ["robustness", "build", "grounded", "--out", str(out), "--transforms", "whitespace,evidence_buried"])
    assert res.exit_code == 0 and "8 original cases, 16 variants" in res.output
    cases = load_cases(out)
    assert len(cases) == 8 + 16 and sum(bool(c.metadata.get("source_case")) for c in cases) == 16
    assert check(cases) == []


def test_build_errors_are_clear(tmp_path):
    assert runner.invoke(app, ["robustness", "build", "grounded", "--transforms", "magic", "--out", str(tmp_path / "x.jsonl")]).exit_code == 1
    assert runner.invoke(app, ["robustness", "build", "nothing_here"]).exit_code == 1


def test_list_shows_transforms():
    out = runner.invoke(app, ["robustness", "list"])
    assert "evidence_buried" in out.output and "paraphrase" in out.output


def test_full_loop_build_run_report(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    out = tmp_path / "g.jsonl"
    runner.invoke(app, ["robustness", "build", "grounded", "--out", str(out), "--transforms", "whitespace,formatting,evidence_buried"])
    run = runner.invoke(app, ["run", "--dataset", str(out), "--model", "mock:smoke"])
    assert run.exit_code == 0, run.output
    run_id = run.output.split("Run ")[1].split()[0].strip("│ ")
    rep = runner.invoke(app, ["robustness", "report", run_id])
    assert rep.exit_code == 0 and "Break rate" in rep.output and "By transform" in rep.output
    plain = runner.invoke(app, ["run", "--model", "mock:smoke"])
    plain_id = plain.output.split("Run ")[1].split()[0].strip("│ ")
    assert runner.invoke(app, ["robustness", "report", plain_id]).exit_code == 1   # No variants in that run.
