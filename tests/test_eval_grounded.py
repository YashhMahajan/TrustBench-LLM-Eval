import asyncio
import json

from helpers import FnProvider
from trustbench.evaluators import evaluate
from trustbench.judge import Judge

CONTEXT = "Customers may request a refund within 30 days."
INPUT = {"context": CONTEXT, "question": "What is the refund period?"}
EV = {"evaluators": [{"type": "grounded_claims"}]}


def fake(claims, labels):
    """A judge that returns the claims first, then a label for each claim."""
    def fn(prompt):
        if "List the factual claims" in prompt:
            return json.dumps({"claims": claims})
        for claim, (label, quote) in labels.items():
            if f"<claim>\n{claim}\n</claim>" in prompt:
                return json.dumps({"label": label, "quote": quote, "rationale": "r", "confidence": 0.9})
    return Judge(FnProvider(fn), "j")


def run(judge, output="answer", spec=None, inp=INPUT):
    ev = {"evaluators": [{"type": "grounded_claims", **(spec or {})}]}
    return asyncio.run(evaluate(ev, inp, {}, output, judge))[0]


def test_all_supported_passes_with_metrics():
    j = fake(["Refunds take 30 days."], {"Refunds take 30 days.": ("supported", "refund within 30 days")})
    r = run(j)
    assert r.passed and r.score == 1.0 and r.details["metrics"]["ec"] == 1.0 and not r.details["human_review"]


def test_unsupported_claim_fails_unless_allowed():
    j = fake(["A", "B"], {"A": ("supported", "refund within 30 days"), "B": ("insufficient", "")})
    r = run(j)
    assert not r.passed and "insufficient: B" in r.rationale
    assert run(j, spec={"max_unsupported": 0.5}).passed


def test_contradicted_claim_always_fails():
    j = fake(["A", "B"], {"A": ("supported", "refund within 30 days"), "B": ("contradicted", "refund within 30 days")})
    assert not run(j, spec={"max_unsupported": 1.0}).passed


def test_answer_without_claims_passes():
    r = run(fake([], {}))
    assert r.passed and "no factual claims" in r.rationale


def test_unjudgeable_claims_fail_and_ask_for_review():
    j = Judge(FnProvider(lambda p: json.dumps({"claims": ["A"]}) if "List the" in p else "garbage"), "j")
    r = run(j)
    assert not r.passed and r.details["human_review"]


def test_sentence_mode_makes_no_extraction_call():
    j = fake([], {"Refunds take 30 days.": ("supported", "refund within 30 days")})
    r = run(j, output="Refunds take 30 days.", spec={"claims": "sentences"})
    assert r.passed and all("List the factual claims" not in p for p in j.provider.prompts)


def test_missing_evidence_or_judge_is_an_evaluator_problem():
    assert run(fake([], {}), inp={"question": "q"}).details == {"error": True}
    assert asyncio.run(evaluate(EV, INPUT, {}, "x", None))[0].details == {"error": True}
