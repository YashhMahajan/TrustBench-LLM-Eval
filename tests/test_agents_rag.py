"""Judge-model checks for RAG answers. The documents come from the app's retrieval span."""

import asyncio
import json

from helpers import FnProvider
from trustbench.evaluators import evaluate
from trustbench.judge import Judge

DOCS = ["Customers may request a refund within 30 days.", "Shipping is free over $50."]
SPAN = [{"kind": "retrieval", "name": "kb", "data": {"docs": DOCS}}]
INPUT = {"question": "What is the refund period?"}


def judge(fn):
    return Judge(FnProvider(fn), "j")


def run(evaluator, j, spans=SPAN, inp=INPUT, expected=None, output="Refunds take 30 days."):
    return asyncio.run(evaluate({"evaluators": [evaluator]}, inp, expected or {}, output, j, spans=spans))[0]


def claim_judge(label, claims=("Refunds take 30 days.",)):
    def fn(prompt):
        if "List the factual claims" in prompt:
            return json.dumps({"claims": list(claims)})
        return json.dumps({"label": label, "quote": "refund within 30 days", "rationale": "r", "confidence": 0.9})
    return judge(fn)


# ---- retrieved documents ----------------------------------------------------------------------------

def test_every_rag_check_needs_a_judge_and_documents():
    for name in ("rag_faithfulness", "rag_context_precision", "rag_context_recall"):
        r = run({"type": name}, None, expected={"answer": "x"})
        assert not r.passed and "No judge" in r.rationale and r.details["error"]
        r = run({"type": name}, claim_judge("supported"), spans=[], expected={"answer": "x"})
        assert not r.passed and "No retrieved documents" in r.rationale and r.details["error"]


def test_documents_can_be_strings_or_objects_and_context_is_the_fallback():
    seen = []

    def fn(prompt):
        seen.append(prompt)
        return json.dumps({"claims": []})
    span = [{"kind": "retrieval", "name": "k", "data": {"docs": [{"id": "d1", "text": "Alpha text"}, "Beta text"]}},
            {"kind": "tool", "name": "ignored", "data": {"docs": ["Not retrieval"]}}]
    run({"type": "rag_faithfulness", "claims": "sentences"}, judge(fn), spans=span, output="Alpha text is what the customer sees.")
    assert "Alpha text" in seen[0] and "Beta text" in seen[0] and "Not retrieval" not in seen[0]
    r = run({"type": "rag_faithfulness"}, claim_judge("supported"), spans=[], inp={"context": DOCS[0], "question": "q"})
    assert r.passed


# ---- faithfulness -----------------------------------------------------------------------------------

def test_a_supported_answer_is_faithful():
    r = run({"type": "rag_faithfulness"}, claim_judge("supported"))
    assert r.passed and r.evaluator == "rag_faithfulness" and r.details["metrics"]["ec"] == 1.0


def test_an_unsupported_or_contradicted_claim_is_not_faithful():
    assert not run({"type": "rag_faithfulness"}, claim_judge("insufficient")).passed
    r = run({"type": "rag_faithfulness", "max_unsupported": 1.0}, claim_judge("contradicted"))
    assert not r.passed and "contradicted" in r.rationale


# ---- answer relevance -------------------------------------------------------------------------------

def test_answer_relevance_uses_the_score_and_the_threshold():
    j = lambda s: judge(lambda p: json.dumps({"rationale": "r", "score": s}))  # noqa: E731
    assert run({"type": "rag_answer_relevance"}, j(3), spans=[]).passed
    assert run({"type": "rag_answer_relevance"}, j(2), spans=[]).passed                      # 2 of 3 is the default bar
    assert not run({"type": "rag_answer_relevance"}, j(1), spans=[]).passed
    assert run({"type": "rag_answer_relevance", "min_score": 1}, j(1), spans=[]).passed
    assert not run({"type": "rag_answer_relevance", "min_score": 3}, j(2), spans=[]).passed


def test_an_unreadable_relevance_reply_is_an_evaluator_error_not_a_model_failure():
    r = run({"type": "rag_answer_relevance"}, judge(lambda p: "garbage"), spans=[])
    assert not r.passed and r.details["error"] is True and "not readable" in r.rationale


# ---- precision and recall ---------------------------------------------------------------------------

def test_context_precision_is_the_share_of_useful_documents():
    j = judge(lambda p: json.dumps({"useful": "refund" in p.split("<document>")[1]}))
    r = run({"type": "rag_context_precision"}, j)
    assert r.score == 0.5 and r.passed and r.details["useful"] == [True, False]
    assert not run({"type": "rag_context_precision", "min": 0.9}, j).passed


def test_context_precision_ignores_documents_the_judge_could_not_read():
    replies = iter(['{"useful": true}', "garbage"])
    r = run({"type": "rag_context_precision"}, judge(lambda p: next(replies)))
    assert r.score == 1.0 and r.details["useful"] == [True, None]
    assert run({"type": "rag_context_precision"}, judge(lambda p: "garbage")).details["human_review"]


def verdict_judge(supported_facts):
    def fn(prompt):
        label = "supported" if any(f"<claim>\n{f}\n</claim>" in prompt for f in supported_facts) else "insufficient"
        return json.dumps({"label": label, "quote": "", "rationale": "r", "confidence": 0.9})
    return judge(fn)


def test_context_recall_is_the_share_of_required_facts_found_in_the_documents():
    exp = {"required_facts": ["30 days", "free shipping over $50", "gift wrap"]}
    r = run({"type": "rag_context_recall", "min": 0.6}, verdict_judge(["30 days", "free shipping over $50"]), expected=exp)
    assert r.passed and abs(r.score - 2 / 3) < 1e-9 and r.details["missing_facts"] == ["gift wrap"]
    assert not run({"type": "rag_context_recall"}, verdict_judge(["30 days"]), expected=exp).passed        # default min is 1.0


def test_context_recall_falls_back_to_the_expected_answer_and_needs_one():
    assert run({"type": "rag_context_recall"}, verdict_judge(["30 days"]), expected={"answer": "30 days"}).passed
    r = run({"type": "rag_context_recall"}, verdict_judge([]), expected={})
    assert not r.passed and r.details["error"]
