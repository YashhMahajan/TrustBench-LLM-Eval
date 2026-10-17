import csv

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from test_engine import ANSWERS, go
from trustbench.annotations import AnnotationError, export_template, import_annotations
from trustbench.cli import app
from trustbench.db import HumanAnnotation
from trustbench.providers import MockProvider


def rows_of(path):
    return list(csv.DictReader(path.open(newline="")))


def test_export_has_one_row_per_answer_with_empty_labels(factory, version, tmp_path):
    run_id = go(factory, version, MockProvider(ANSWERS))
    out = tmp_path / "a.csv"
    assert export_template(factory, run_id, out) == 3
    rows = rows_of(out)
    assert {r["case_id"] for r in rows} == {"a", "b", "c"} and all(r["label"] == "" for r in rows)
    assert next(r for r in rows if r["case_id"] == "a")["reference"] == "Paris"


def test_failed_calls_are_not_exported(factory, version, tmp_path):
    run_id = go(factory, version, MockProvider(always_fail=True))
    assert export_template(factory, run_id, tmp_path / "a.csv") == 0


def test_import_stores_labels_skips_empty_and_updates_on_repeat(factory, version, tmp_path):
    run_id = go(factory, version, MockProvider(ANSWERS))
    out = tmp_path / "a.csv"
    export_template(factory, run_id, out)
    rows = rows_of(out)
    rows[0]["label"], rows[1]["label"] = "3", "0"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    assert import_annotations(factory, out, "ana", "correctness") == (2, 1)
    rows[0]["label"] = "2"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    assert import_annotations(factory, out, "ana", "correctness") == (2, 1)
    with factory() as s:
        labels = sorted(a.label for a in s.scalars(select(HumanAnnotation)))
    assert labels == [0.0, 2.0]  # The first label was replaced, not added.


def test_bad_rows_are_reported_with_a_line_number(factory, version, tmp_path):
    bad = tmp_path / "b.csv"
    bad.write_text("response_id,label\n1,high\n")
    with pytest.raises(AnnotationError, match="Line 2"):
        import_annotations(factory, bad, "ana", "correctness")
    bad.write_text("response_id,label\n999,1\n")
    with pytest.raises(AnnotationError, match="does not exist"):
        import_annotations(factory, bad, "ana", "correctness")


def test_cli_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    runner = CliRunner()
    out = runner.invoke(app, ["run", "--model", "mock:smoke"])
    run_id = out.output.split("Run ")[1].split()[0].strip("│ ")
    csv_path = tmp_path / "a.csv"
    assert runner.invoke(app, ["annotations", "export", run_id, "--out", str(csv_path)]).exit_code == 0
    rows = rows_of(csv_path)
    rows[0]["label"] = "1"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    res = runner.invoke(app, ["annotations", "import", str(csv_path), "--annotator", "ana"])
    assert res.exit_code == 0 and "Stored 1" in res.output
