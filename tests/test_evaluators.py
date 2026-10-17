from trustbench.evaluators import evaluate


def one(evaluation, expected, output):
    return evaluate(evaluation, expected, output)[0]


def test_exact_match_ignores_case_space_and_punctuation():
    assert one({}, {"answer": "Paris"}, "  paris. ").passed
    assert not one({}, {"answer": "Paris"}, "Lyon").passed


def test_exact_match_accepts_any_listed_answer():
    assert one({}, {"answers": ["4", "four"]}, "Four").passed


def test_contains_scores_the_share_of_found_facts():
    r = one({"evaluators": [{"type": "contains", "values": ["30 days", "refund"]}]}, {}, "A refund in 10 days")
    assert not r.passed and r.score == 0.5


def test_rule_reports_each_failure():
    spec = {"evaluators": [{"type": "rule", "max_words": 3, "forbidden": ["blue"]}]}
    r = one(spec, {}, "the sea is very blue")
    assert not r.passed and "5 words" in r.rationale and "blue" in r.rationale
    assert one(spec, {}, "calm sea").passed


def test_missing_reference_fails_instead_of_passing():
    assert not one({}, {}, "anything").passed
