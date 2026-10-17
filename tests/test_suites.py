"""Every built-in suite must be clean and must run from start to finish."""

import json
from pathlib import Path

import pytest

from helpers import FnProvider
from trustbench import quality, report
from trustbench.datasets import SUITES_DIR, import_dataset, load_cases
from trustbench.engine import RunOptions, run_eval
from trustbench.providers import MockProvider

SUITES = sorted(p for p in SUITES_DIR.glob("*.jsonl"))


def judge_reply(prompt):
    """A judge that answers every kind of judge prompt with something readable."""
    if "List the factual claims" in prompt:
        return json.dumps({"claims": ["A claim."]})
    if "Check the claim" in prompt:
        return json.dumps({"label": "insufficient", "quote": "", "rationale": "r", "confidence": 0.9})
    return json.dumps({"score": 1, "rationale": "r"})


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.stem)
def test_suite_is_clean(path):
    cases = load_cases(path)
    assert len(cases) >= (5 if path.stem == "smoke" else 8)
    assert quality.check(cases) == []


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.stem)
def test_suite_runs_without_evaluator_errors(path, factory):
    with factory() as s:
        version = import_dataset(s, path.stem, load_cases(path))
    providers = {"mock": MockProvider(), "judge": FnProvider(judge_reply)}
    run_id = run_eval(factory, version.id, ["mock:m"], RunOptions(backoff=0, judge="judge:j"),
                      provider_factory=lambda n: providers[n])
    rows = report.case_rows(factory, run_id)
    assert len(rows) == version.case_count and all(r["status"] == "done" for r in rows)
    assert not any("Evaluator error" in r["rationale"] for r in rows)
