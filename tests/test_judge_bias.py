import asyncio
import json

from helpers import FnProvider
from trustbench.datasets import CaseSpec
from trustbench.judge import RUBRICS, Judge
from trustbench.judge_bias import FILLER, run_bias_probes

CASES = [CaseSpec(id=f"c{i}", input=f"Question {i}?", expected={"answer": f"answer {i}"}) for i in range(4)]
R = RUBRICS["correctness"]


def section(prompt, tag):
    return prompt.split(f"<{tag}>\n")[1].split(f"\n</{tag}>")[0]


def fair_judge(prompt):
    if "<first_answer>" in prompt:
        ref, first = section(prompt, "reference_answer"), section(prompt, "first_answer")
        return json.dumps({"winner": "first" if first == ref else "second"})
    ref, ans = section(prompt, "reference_answer"), section(prompt, "answer_to_grade")
    return json.dumps({"score": 3 if ans.startswith(ref) else 0})


def run(fn, **kw):
    return asyncio.run(run_bias_probes(Judge(FnProvider(fn), "j"), CASES, R, **kw))


def test_fair_judge_shows_no_bias():
    r = run(fair_judge)
    assert (r.position_inconsistency, r.correct_pick_rate) == (0.0, 1.0)
    assert (r.verbosity_mean_delta, r.wording_mean_abs_change) == (0.0, 0.0)
    assert (r.cases, r.position_pairs, r.errors) == (4, 4, 0)


def test_position_biased_judge_is_caught():
    def fn(prompt):
        return json.dumps({"winner": "first"}) if "<first_answer>" in prompt else fair_judge(prompt)
    r = run(fn)
    assert r.position_inconsistency == 1.0 and r.correct_pick_rate == 0.0


def test_length_biased_judge_is_caught():
    def fn(prompt):
        if "<answer_to_grade>" in prompt:
            return json.dumps({"score": 3 if FILLER in prompt else 1})
        return fair_judge(prompt)
    assert run(fn).verbosity_mean_delta == 2.0


def test_wording_sensitive_judge_is_caught():
    def fn(prompt):
        if "<answer_to_grade>" in prompt:
            return json.dumps({"score": 0 if "PLEASE ANSWER" in prompt.upper() and "QUESTION 1?" in prompt else 3})
        return fair_judge(prompt)
    assert run(fn).wording_mean_abs_change > 0


def test_unreadable_judge_replies_are_counted_not_raised():
    r = run(lambda p: "garbage")
    assert r.errors == 4 and r.position_inconsistency is None


def test_max_cases_and_cases_without_reference():
    r = run(fair_judge, max_cases=2)
    assert r.cases == 2
    no_ref = asyncio.run(run_bias_probes(Judge(FnProvider(fair_judge), "j"), [CaseSpec(id="x", input="q", evaluation={"evaluators": [{"type": "rule"}]})], R))
    assert no_ref.cases == 0 and no_ref.position_inconsistency is None
