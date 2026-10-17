import random

import pytest

from trustbench.datasets import CaseSpec, load_cases, resolve_path
from trustbench.transforms import TRANSFORMS, build_variants, check_invariants, protected_words, split_sentences

CTX = "The shop opens at 9 am. Parking is free for 2 hours. Dogs are welcome inside. The cafe closes at 5 pm."
CASE = CaseSpec(id="g1", task="grounded_qa", input={"context": CTX, "question": "For how long is parking free? Answer briefly."},
                expected={"required_facts": ["2 hours"]}, evaluation={"evaluators": [{"type": "contains", "values": ["2 hours"]}]})
EVIDENCE = "Parking is free for 2 hours."


def apply(name, case=CASE, seed=0):
    return TRANSFORMS[name].fn(case, random.Random(seed))


@pytest.mark.parametrize("name", list(TRANSFORMS))
def test_every_transform_keeps_the_meaning(name):
    v = apply(name)
    assert v is not None, name
    assert check_invariants(CASE, v) == []
    assert v.input != CASE.input


def test_transforms_are_repeatable():
    assert apply("typos", seed=3) == apply("typos", seed=3)


def test_whitespace_changes_only_spacing():
    v = apply("whitespace")
    assert v.input["question"] != CASE.input["question"]
    assert " ".join(v.input["question"].split()) == CASE.input["question"]
    assert " ".join(v.input["context"].split()) == CTX


def test_typos_never_touch_words_that_decide_the_answer():
    case = CaseSpec(id="r", input={"prompt": "Describe the ocean in a sentence. Do not use the word 'blue'. Mention water and waves clearly."},
                    evaluation={"evaluators": [{"type": "rule", "forbidden": ["blue"]}]})
    for seed in range(40):
        v = TRANSFORMS["typos"].fn(case, random.Random(seed))
        if v:
            assert "blue" in v.input["prompt"] and check_invariants(case, v) == []
    assert "blue" in protected_words(case)


def test_typos_do_change_ordinary_words_and_keep_the_context_exact():
    changed = [TRANSFORMS["typos"].fn(CASE, random.Random(s)) for s in range(30)]
    assert any(v and v.input["question"] != CASE.input["question"] for v in changed)
    assert all(v is None or v.input["context"] == CTX for v in changed)


def test_reorder_keeps_all_sentences_and_skips_order_sensitive_cases():
    v = apply("reorder_context")
    assert sorted(split_sentences(v.input["context"])) == sorted(split_sentences(CTX))
    assert apply("reorder_context", CASE.model_copy(update={"metadata": {"order_sensitive": True}})) is None
    short = CASE.model_copy(update={"input": {"context": "One. Two.", "question": "q"}})
    assert apply("reorder_context", short) is None


def test_evidence_moves_to_the_requested_place():
    assert split_sentences(apply("evidence_first").input["context"])[0] == EVIDENCE
    assert split_sentences(apply("evidence_last").input["context"])[-1] == EVIDENCE
    buried = split_sentences(apply("evidence_buried").input["context"])
    assert len(buried) == len(split_sentences(CTX)) + 10 and 5 < buried.index(EVIDENCE) < len(buried) - 1


def test_transforms_that_do_not_fit_return_nothing():
    plain = CaseSpec(id="p", input={"prompt": "What is 2 plus 2?"}, expected={"answer": "4"})
    for name in ("reorder_context", "evidence_first", "evidence_last", "evidence_buried"):
        assert apply(name, plain) is None


def test_invariant_checker_catches_bad_variants():
    bad_expected = CASE.model_copy(update={"expected": {"required_facts": ["6 pm"]}})
    assert "expected" in check_invariants(CASE, bad_expected)[0]
    bad_number = CASE.model_copy(update={"input": {**CASE.input, "context": CTX.replace("9 am", "8 am")}})
    problems = " ".join(check_invariants(CASE, bad_number))
    assert "numbers" in problems and "context was lost" in problems
    lost_evidence = CASE.model_copy(update={"input": {**CASE.input, "context": "The shop opens at 9 am."}})
    assert any("evidence" in p for p in check_invariants(CASE, lost_evidence))


def test_build_variants_names_links_and_reports():
    other = CaseSpec(id="plain", input={"prompt": "What is 2 plus 2?"}, expected={"answer": "4"})
    variants, rep = build_variants([CASE, other], ["whitespace", "evidence_buried"])
    assert {v.id for v in variants} == {"g1~whitespace", "g1~evidence_buried", "plain~whitespace"}
    v = next(v for v in variants if v.id == "g1~whitespace")
    assert v.metadata["source_case"] == "g1" and v.metadata["transform"] == "whitespace"
    assert rep.applied == {"whitespace": 2, "evidence_buried": 1} and rep.not_applicable == {"whitespace": 0, "evidence_buried": 1}
    again, _ = build_variants([CASE, other, *variants], ["whitespace"])
    assert len(again) == 2   # No variants of variants.


def test_unknown_transform_is_an_error():
    with pytest.raises(ValueError, match="Unknown transform"):
        build_variants([CASE], ["magic"])


@pytest.mark.parametrize("suite", ["grounded", "abstention", "factual", "instruction_following", "structured_output"])
def test_variants_of_the_builtin_suites_are_all_fair(suite):
    cases = load_cases(resolve_path(suite))
    variants, rep = build_variants(cases, list(TRANSFORMS))
    assert variants and sum(rep.rejected.values()) == 0   # Our own transforms make no unfair variants.
    by_id = {c.id: c for c in cases}
    for v in variants:
        assert check_invariants(by_id[v.metadata["source_case"]], v) == []
