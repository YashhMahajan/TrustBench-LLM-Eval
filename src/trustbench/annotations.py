"""Human labels. Export a CSV, label it in a spreadsheet, import it again."""

import csv
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .datasets import render_prompt
from .db import HumanAnnotation, ModelConfiguration, ModelResponse, TestCase, TestExecution
from .eval_judge import reference_of

COLUMNS = ["response_id", "case_id", "model", "question", "reference", "output", "label", "note"]


def export_template(factory: sessionmaker, run_id: str, path: Path) -> int:
    """Write one row for each answer. The label column is empty."""
    with factory() as s:
        execs = s.execute(
            select(TestExecution, TestCase, ModelConfiguration)
            .join(TestCase, TestCase.id == TestExecution.test_case_id)
            .join(ModelConfiguration, ModelConfiguration.id == TestExecution.model_config_id)
            .where(TestExecution.run_id == run_id, TestExecution.status == "done")
            .order_by(TestCase.id, ModelConfiguration.id, TestExecution.repetition)).all()
        rows = []
        for ex, case, mc in execs:
            last = s.scalars(select(ModelResponse).where(ModelResponse.execution_id == ex.id,
                                                         ModelResponse.ok.is_(True))
                             .order_by(ModelResponse.attempt.desc())).first()
            rows.append({"response_id": last.id, "case_id": case.case_id, "model": f"{mc.provider}:{mc.model}",
                         "question": render_prompt(case.input), "reference": reference_of(case.expected) or "",
                         "output": last.output, "label": "", "note": ""})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


class AnnotationError(ValueError):
    pass


def import_annotations(factory: sessionmaker, path: Path, annotator: str, dimension: str) -> tuple[int, int]:
    """Store the labels. A new label for the same answer, person and dimension replaces the old one.

    Returns (stored, skipped). Rows with an empty label are skipped.
    """
    stored = skipped = 0
    with factory.begin() as s:
        for n, row in enumerate(csv.DictReader(path.open(newline="")), start=2):
            raw = (row.get("label") or "").strip()
            if not raw:
                skipped += 1
                continue
            try:
                label, response_id = float(raw), int(row["response_id"])
            except (ValueError, KeyError):
                raise AnnotationError(f"Line {n}: label '{raw}' or response_id is not a number.") from None
            if s.get(ModelResponse, response_id) is None:
                raise AnnotationError(f"Line {n}: response {response_id} does not exist.")
            existing = s.scalar(select(HumanAnnotation).where(
                HumanAnnotation.response_id == response_id, HumanAnnotation.annotator == annotator,
                HumanAnnotation.dimension == dimension))
            if existing:
                existing.label, existing.note = label, row.get("note", "")
            else:
                s.add(HumanAnnotation(response_id=response_id, annotator=annotator, dimension=dimension,
                                      label=label, note=row.get("note", "")))
            stored += 1
    return stored, skipped
