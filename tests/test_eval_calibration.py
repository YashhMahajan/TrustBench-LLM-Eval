import asyncio

import pytest

from trustbench.eval_calibration import parse_confidence
from trustbench.evaluators import evaluate


def run(output, answer="Paris"):
    ev = {"evaluators": [{"type": "calibrated_answer"}]}
    return asyncio.run(evaluate(ev, {}, {"answer": answer}, output))[0]


@pytest.mark.parametrize("text,value", [("Confidence: 85%", 0.85), ("confidence 90", 0.9),
                                        ("Confidence: 0.7", 0.7), ("Confidence - 100%", 1.0), ("no value", None),
                                        ("Confidence: 250%", None)])
def test_parse_confidence(text, value):
    assert parse_confidence(text) == value


def test_correct_answer_records_confidence():
    r = run("Answer: Paris\nConfidence: 95%")
    assert r.passed and r.details == {"correct": True, "confidence": 0.95}


def test_wrong_answer_still_records_confidence():
    r = run("Answer: Lyon\nConfidence: 90%")
    assert not r.passed and r.details == {"correct": False, "confidence": 0.9}


def test_missing_confidence_or_answer_line_fails_but_keeps_correctness():
    r = run("Answer: Paris")
    assert not r.passed and r.details["correct"] and "confidence" in r.rationale
    assert "answer line" in run("It is Paris. Confidence: 80%").rationale


def test_answer_must_match_whole_words():
    assert not run("Answer: 17\nConfidence: 50%", answer="7").passed
    assert run("Answer: It is 7.\nConfidence: 50%", answer="7").passed
