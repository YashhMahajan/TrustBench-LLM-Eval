import json

from sqlalchemy import select

from helpers import FnProvider
from trustbench.datasets import CaseSpec, import_dataset
from trustbench.db import HumanAnnotation, ModelResponse, TestCase, TestExecution
from trustbench.engine import RunOptions, run_eval
from trustbench.judge_validation import MIN_LABELS, validate_judge
from trustbench.providers import MockProvider


def make_run(factory, judge_scores):
    """A run where case i gets judge score judge_scores[i]."""
    cases = [CaseSpec(id=f"c{i}", input=f"Question number {i}", expected={"answer": "ref"},
                      evaluation={"evaluators": [{"type": "llm_judge"}]}) for i in range(len(judge_scores))]
    with factory() as s:
        v = import_dataset(s, "jv", cases)
    judge = FnProvider(lambda p: json.dumps({"score": judge_scores[int(p.split("Question number ")[1].split("\n")[0])]}))
    providers = {"mock": MockProvider({"Question": "an answer"}), "judge": judge}
    return run_eval(factory, v.id, ["mock:m"], RunOptions(backoff=0, judge="judge:j"),
                    provider_factory=lambda n: providers[n])


def label(factory, annotator, labels):
    """Label the answers by case. labels[i] is the label for case c{i}."""
    with factory.begin() as s:
        rows = s.execute(select(ModelResponse.id, TestCase.case_id)
                         .join(TestExecution, TestExecution.id == ModelResponse.execution_id)
                         .join(TestCase, TestCase.id == TestExecution.test_case_id)).all()
        for rid, case_id in rows:
            s.add(HumanAnnotation(response_id=rid, annotator=annotator, dimension="correctness",
                                  label=labels[int(case_id[1:])]))


def test_agreement_numbers_and_warnings(factory):
    run_id = make_run(factory, [3, 3, 0, 0, 2, 1])
    label(factory, "ana", [3, 3, 0, 1, 2, 3])
    v = validate_judge(factory, run_id)
    assert v.n == 6 and v.exact_agreement == 4 / 6
    assert v.exact_agreement_ci[0] < 4 / 6 < v.exact_agreement_ci[1]
    assert any("Only 6 labelled" in w for w in v.warnings) and any("One annotator" in w for w in v.warnings)


def test_false_pass_and_false_fail_rates(factory):
    # The judge passes c0 and c1 (pass level is 2). People pass c0 and c3.
    run_id = make_run(factory, [3, 3, 0, 0])
    label(factory, "ana", [3, 0, 0, 3])
    v = validate_judge(factory, run_id)
    assert v.false_pass_rate == 0.5   # Of the 2 answers people fail (c1, c2), the judge passed c1.
    assert v.false_fail_rate == 0.5   # Of the 2 answers people pass (c0, c3), the judge failed c3.
    assert v.pass_agreement == 0.5


def test_two_annotators_give_human_alpha(factory):
    run_id = make_run(factory, [3, 2, 1, 0, 3, 2, 1, 0])
    label(factory, "ana", [3, 2, 1, 0, 3, 2, 1, 0])
    label(factory, "ben", [3, 2, 1, 0, 3, 2, 1, 0])
    v = validate_judge(factory, run_id)
    assert v.human_alpha == 1.0 and v.kappa == 1.0 and v.exact_agreement == 1.0
    assert not any("One annotator" in w for w in v.warnings)


def test_no_labels_gives_a_clear_warning(factory):
    v = validate_judge(factory, make_run(factory, [3, 2]))
    assert v.n == 0 and "No response has both" in v.warnings[0]


def test_enough_labels_remove_the_small_sample_warning(factory):
    n = MIN_LABELS
    scores = [i % 4 for i in range(n)]
    run_id = make_run(factory, scores)
    label(factory, "ana", scores)
    v = validate_judge(factory, run_id)
    assert v.n == n and not any("Only" in w for w in v.warnings)
