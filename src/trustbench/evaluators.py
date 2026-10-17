"""Deterministic evaluators. Each one returns a pass or fail with a reason."""

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class Outcome:
    evaluator: str
    rubric_version: str
    passed: bool
    score: float
    rationale: str


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text.strip(" .!?,;:\"'")


def exact_match(expected: dict, output: str, spec: dict) -> Outcome:
    answers = expected.get("answers") or ([expected["answer"]] if "answer" in expected else [])
    if not answers:
        return Outcome("exact_match", "1", False, 0.0, "No reference answer in the test case.")
    got = _normalize(output)
    ok = any(got == _normalize(str(a)) for a in answers)
    return Outcome("exact_match", "1", ok, float(ok), "Match." if ok else f"Expected one of {answers}.")


def contains(expected: dict, output: str, spec: dict) -> Outcome:
    values = spec.get("values") or expected.get("required_facts") or []
    if not values:
        return Outcome("contains", "1", False, 0.0, "No required values given.")
    text = output.lower()
    missing = [v for v in values if str(v).lower() not in text]
    score = 1 - len(missing) / len(values)
    return Outcome("contains", "1", not missing, score, "All found." if not missing else f"Missing: {missing}.")


def rule(expected: dict, output: str, spec: dict) -> Outcome:
    failures = []
    words = len(output.split())
    if "max_words" in spec and words > spec["max_words"]:
        failures.append(f"{words} words, maximum is {spec['max_words']}")
    if "min_words" in spec and words < spec["min_words"]:
        failures.append(f"{words} words, minimum is {spec['min_words']}")
    if "regex" in spec and not re.search(spec["regex"], output):
        failures.append(f"no match for /{spec['regex']}/")
    for word in spec.get("forbidden", []):
        if word.lower() in output.lower():
            failures.append(f"contains forbidden text '{word}'")
    return Outcome("rule", "1", not failures, 0.0 if failures else 1.0, "; ".join(failures) or "All rules hold.")


EVALUATORS: dict[str, Callable[[dict, str, dict], Outcome]] = {
    "exact_match": exact_match,
    "contains": contains,
    "rule": rule,
}


def evaluate(evaluation: dict, expected: dict, output: str) -> list[Outcome]:
    """Run each evaluator in the test case. Default: exact match on the answer."""
    specs = evaluation.get("evaluators") or [{"type": "exact_match"}]
    return [EVALUATORS[s["type"]](expected, output, s) for s in specs]
