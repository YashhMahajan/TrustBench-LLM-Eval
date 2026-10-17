import json

import pytest
from sqlalchemy import func, select

from trustbench.datasets import CaseSpec, DatasetError, content_hash, import_dataset, load_cases, validate
from trustbench.db import DatasetVersion, TestCase


def test_same_content_gives_same_version(factory, cases):
    with factory() as s:
        v1 = import_dataset(s, "t", cases)
        v2 = import_dataset(s, "t", list(reversed(cases)))
        assert v1.id == v2.id
        assert s.scalar(select(func.count()).select_from(DatasetVersion)) == 1


def test_changed_content_makes_new_version_and_keeps_old(factory, cases):
    with factory() as s:
        v1 = import_dataset(s, "t", cases)
        changed = [cases[0].model_copy(update={"expected": {"answer": "Lyon"}}), *cases[1:]]
        v2 = import_dataset(s, "t", changed)
        assert v1.id != v2.id
        old = s.scalar(select(TestCase).where(TestCase.dataset_version_id == v1.id, TestCase.case_id == "a"))
        assert old.expected == {"answer": "Paris"}


def test_duplicate_ids_rejected():
    c = CaseSpec(id="x", input="q", expected={"answer": "a"})
    with pytest.raises(DatasetError, match="Duplicate"):
        validate([c, c])


def test_unknown_evaluator_rejected():
    c = CaseSpec(id="x", input="q", evaluation={"evaluators": [{"type": "magic"}]})
    with pytest.raises(DatasetError, match="unknown evaluator"):
        validate([c])


def test_case_without_reference_rejected():
    with pytest.raises(DatasetError, match="expected answer"):
        validate([CaseSpec(id="x", input="q")])


def test_load_jsonl_and_csv(tmp_path):
    j = tmp_path / "d.jsonl"
    j.write_text(json.dumps({"id": "1", "input": "q", "expected": {"answer": "a"}}) + "\n")
    assert load_cases(j)[0].input == {"prompt": "q"}
    c = tmp_path / "d.csv"
    c.write_text("id,prompt,answer,tag\n1,q,a,easy\n")
    case = load_cases(c)[0]
    assert case.expected == {"answer": "a"} and case.metadata == {"tag": "easy"}


def test_hash_ignores_order(cases):
    assert content_hash(cases) == content_hash(list(reversed(cases)))
