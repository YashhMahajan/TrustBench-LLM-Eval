import asyncio
import json

from helpers import FnProvider
from trustbench.datasets import CaseSpec
from trustbench.judge import Judge
from trustbench.paraphrase import build_paraphrases, check_paraphrase, paraphrase_case

CASE = CaseSpec(id="q1", input={"prompt": "Describe the sea in 10 words or fewer. Do not use the word 'blue'."},
                evaluation={"evaluators": [{"type": "rule", "max_words": 10, "forbidden": ["blue"]}]})


def judge(rewrite, same=True):
    def fn(prompt):
        if "exactly the same thing" in prompt:
            return json.dumps({"same": same})
        return json.dumps({"text": rewrite})
    return Judge(FnProvider(fn), "j")


def run(rewrite, same=True, case=CASE):
    return asyncio.run(paraphrase_case(judge(rewrite, same), case))


GOOD = "Give a description of the ocean using at most 10 words, and avoid the word 'blue'."


def test_good_paraphrase_becomes_a_linked_variant():
    v, reason = run(GOOD)
    assert reason == "" and v.id == "q1~paraphrase" and v.input["prompt"] == GOOD
    assert v.metadata == {"source_case": "q1", "transform": "paraphrase"} and v.evaluation == CASE.evaluation


def test_each_check_can_reject():
    assert run(CASE.input["prompt"])[1] == "the text is the same"
    assert run("Describe the sea in 12 words or fewer. Do not use the word 'blue'.")[1] == "the numbers changed"
    assert run("Describe the sea in 10 words or fewer without a colour word.")[1] == "the word 'blue' was lost"
    assert run("Describe the sea in 10 words or fewer, never saying 'blue'. " + "Be long. " * 40)[1] == "the length changed too much"
    assert run(GOOD, same=False)[1] == "the judge says the meaning changed"


def test_unreadable_replies_are_rejected_not_raised():
    bad = Judge(FnProvider(lambda p: "garbage"), "j")
    assert asyncio.run(paraphrase_case(bad, CASE)) == (None, "unreadable reply")
    half = Judge(FnProvider(lambda p: json.dumps({"text": GOOD}) if "Rewrite" in p else "garbage"), "j")
    assert asyncio.run(paraphrase_case(half, CASE)) == (None, "unreadable check")


def test_cases_without_question_text_are_skipped():
    case = CaseSpec(id="n", input={"context": "x"}, expected={"answer": "a"})
    assert asyncio.run(paraphrase_case(judge(GOOD), case)) == (None, "no question text")


def test_the_context_is_never_changed():
    case = CaseSpec(id="c", input={"context": "Refunds take 30 days.", "question": "How long do refunds take?"},
                    expected={"required_facts": ["30 days"]})
    v, _ = run("What is the length of time for a refund?", case=case)
    assert v.input["context"] == "Refunds take 30 days." and v.input["question"].startswith("What is the length")


def test_build_counts_reasons_and_skips_variants():
    other = CaseSpec(id="o", input={"prompt": "Name the capital of France."}, expected={"answer": "Paris"})
    variant = CASE.model_copy(update={"id": "q1~whitespace", "metadata": {"source_case": "q1"}})
    out, rep = asyncio.run(build_paraphrases(judge(GOOD), [CASE, other, variant]))
    assert [v.id for v in out] == ["q1~paraphrase"] and rep.applied == 1
    assert rep.rejected == {"the numbers changed": 1}   # The reply for 'o' reuses the text of q1, so its numbers differ.


def test_check_paraphrase_keeps_words_that_decide_the_answer():
    assert check_paraphrase(CASE, CASE.input["prompt"], "Write about the sea in at most 10 words. Avoid the word 'blue'.") is None
