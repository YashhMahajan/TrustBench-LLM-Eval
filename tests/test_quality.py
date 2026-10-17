from trustbench.datasets import CaseSpec
from trustbench.quality import check, check_duplicates_and_conflicts, check_references


def case(id, text, **kw):
    kw.setdefault("expected", {"answer": "x"})
    return CaseSpec(id=id, input=text, **kw)


def codes(issues):
    return sorted(i.code for i in issues)


def test_clean_dataset_has_no_issues():
    assert check([case("a", "What is the capital of France?"), case("b", "How many legs has a spider?")]) == []


def test_exact_duplicate_ignores_case_and_punctuation():
    issues = check_duplicates_and_conflicts([case("a", "What is 2+2?"), case("b", "what is 2 + 2")])
    assert codes(issues) == ["duplicate"] and issues[0].case_ids == ["a", "b"]


def test_same_input_with_different_answers_is_an_error():
    issues = check_duplicates_and_conflicts([
        case("a", "Capital of France?", expected={"answer": "Paris"}),
        case("b", "capital of france", expected={"answer": "Lyon"})])
    assert [(i.code, i.severity) for i in issues] == [("conflicting_answers", "error")]


def test_near_duplicates_are_found_but_different_questions_are_not():
    long = "Please read the following policy text and tell me how many days a customer has to ask for a refund"
    issues = check_duplicates_and_conflicts([
        case("a", long), case("b", long + " today"), case("c", "Name the largest planet in the solar system please")])
    assert [(i.code, i.case_ids) for i in issues] == [("near_duplicate", ["a", "b"])]


def test_missing_references_per_evaluator():
    cases = [
        case("a", "q1", expected={}, evaluation={"evaluators": [{"type": "numeric"}]}),
        case("b", "q2", expected={}, evaluation={"evaluators": [{"type": "abstain"}]}),
        case("c", "q3", expected={}, evaluation={"evaluators": [{"type": "json_schema"}]}),
        case("d", "q4", expected={}, evaluation={"evaluators": [{"type": "grounded_claims"}]}),
        case("e", "q5", expected={}, evaluation={"evaluators": [{"type": "rule", "max_words": 3}]}),
    ]
    issues = check_references(cases)
    assert sorted(i.case_ids[0] for i in issues) == ["a", "b", "c", "d"]


def test_judge_without_reference_is_a_warning():
    issues = check_references([case("a", "q", expected={}, evaluation={"evaluators": [{"type": "llm_judge"}]})])
    assert [(i.code, i.severity) for i in issues] == [("judge_without_reference", "warning")]
