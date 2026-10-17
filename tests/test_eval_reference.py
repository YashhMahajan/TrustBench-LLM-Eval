import asyncio

import pytest

from trustbench.eval_reference import f1
from trustbench.evaluators import evaluate


def run(spec, expected, output):
    return asyncio.run(evaluate({"evaluators": [spec]}, {}, expected, output))[0]


def test_f1_basics():
    assert f1("the Eiffel Tower", "Eiffel Tower") == 1.0
    assert f1("Paris", "Lyon") == 0.0
    assert 0.5 < f1("It is in Paris France", "Paris France") < 1.0


def test_token_f1_uses_best_reference_and_threshold():
    exp = {"answers": ["thirty days", "30 days"]}
    assert run({"type": "token_f1"}, exp, "30 days").passed
    assert not run({"type": "token_f1", "threshold": 0.9}, {"answer": "30 days"}, "within 30 days of purchase").passed


@pytest.mark.parametrize("text", [
    "I do not know.", "The context does not mention a phone number.", "I can't help with that.",
    "That information is not provided.", "There is insufficient information.",
])
def test_abstain_phrases_detected(text):
    assert run({"type": "abstain"}, {"abstain": True}, text).passed


def test_failure_to_abstain_and_unnecessary_refusal_are_different_failures():
    a = run({"type": "abstain"}, {"abstain": True}, "The number is 555-0100.")
    assert not a.passed and a.details["failure"] == "failure_to_abstain"
    b = run({"type": "abstain"}, {"abstain": False}, "I cannot answer that.")
    assert not b.passed and b.details["failure"] == "unnecessary_refusal"
    assert run({"type": "abstain"}, {"abstain": False}, "Paris.").passed


def test_custom_phrases_and_missing_flag():
    assert run({"type": "abstain", "phrases": ["no idea"]}, {"abstain": True}, "No idea!").passed
    assert run({"type": "abstain"}, {}, "x").details == {"error": True}
