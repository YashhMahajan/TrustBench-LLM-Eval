import asyncio
import json

from helpers import FnProvider
from trustbench.judge import Judge
from trustbench.verify import verify_claim, verify_claims

EVIDENCE = "Customers may request a refund within 30 days.\nShipping is free."


def judge(**reply):
    return Judge(FnProvider(lambda p: json.dumps(reply)), "j")


def verify(claim="x", evidence=EVIDENCE, **reply):
    return asyncio.run(verify_claim(judge(**reply), claim, evidence))


def test_supported_with_real_quote_needs_no_review():
    v = verify(label="supported", quote="refund within 30 days", rationale="r", confidence=0.9)
    assert (v.label, v.needs_review, v.confidence) == ("supported", False, 0.9)


def test_quote_may_differ_in_case_and_spacing():
    assert not verify(label="supported", quote="Shipping   IS free", confidence=0.9).needs_review


def test_made_up_quote_is_flagged_for_review():
    v = verify(label="supported", quote="refund within 90 days", confidence=0.95)
    assert v.label == "supported" and v.needs_review


def test_contradicted_without_quote_and_low_confidence_are_flagged():
    assert verify(label="contradicted", confidence=0.9).needs_review
    assert verify(label="insufficient", confidence=0.3).needs_review
    assert not verify(label="insufficient", confidence=0.9).needs_review


def test_no_evidence_means_not_applicable_without_a_model_call():
    j = judge(label="supported")
    v = asyncio.run(verify_claim(j, "x", ""))
    assert v.label == "not_applicable" and j.provider.prompts == []


def test_unreadable_or_unknown_label_is_not_applicable_and_flagged():
    for reply in ({"label": "maybe"}, {"nothing": 1}):
        v = verify(**reply)
        assert v.label == "not_applicable" and v.needs_review
    bad = asyncio.run(verify_claim(Judge(FnProvider(lambda p: "garbage"), "j"), "x", EVIDENCE))
    assert bad.label == "not_applicable"


def test_verify_claims_keeps_order():
    j = Judge(FnProvider(lambda p: json.dumps({"label": "contradicted" if "DOGS" in p else "insufficient"})), "j")
    out = asyncio.run(verify_claims(j, ["cats", "DOGS"], EVIDENCE))
    assert [v.claim for v in out] == ["cats", "DOGS"] and [v.label for v in out] == ["insufficient", "contradicted"]
