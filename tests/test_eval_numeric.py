import asyncio

import pytest

from trustbench.evaluators import evaluate


def run(output, number=42, **spec):
    ev = {"evaluators": [{"type": "numeric", **spec}]}
    return asyncio.run(evaluate(ev, {}, {"number": number}, output))[0]


@pytest.mark.parametrize("text", ["42", "The answer is 42.", "It is 1,000 - 958 = 42", "-3 then 42"])
def test_takes_last_number_by_default(text):
    assert run(text).passed


def test_first_number_option():
    assert run("42 apples and 7 pears", pick="first").passed
    assert not run("42 apples and 7 pears").passed


def test_tolerance():
    assert run("41.9", abs_tol=0.2).passed
    assert run("43", rel_tol=0.05).passed
    assert not run("45", rel_tol=0.05).passed


def test_thousands_separator_and_decimals():
    assert run("1,200.50", number=1200.5).passed


def test_no_number_and_wrong_number_explain_themselves():
    assert "No number" in run("forty-two").rationale
    assert "Got 7" in run("7").rationale


def test_missing_expected_number_is_an_evaluator_problem():
    ev = {"evaluators": [{"type": "numeric"}]}
    assert asyncio.run(evaluate(ev, {}, {}, "1"))[0].details == {"error": True}
