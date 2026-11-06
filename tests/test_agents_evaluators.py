"""tool_call, trajectory, task_success: what the app did, checked in code."""

import asyncio

import pytest

from trustbench.datasets import CaseSpec, DatasetError, validate
from trustbench.evaluators import evaluate


def run(evaluator: dict, calls=None, expected=None, output="done", inp=None):
    return asyncio.run(evaluate({"evaluators": [evaluator]}, inp or {}, expected or {}, output, None, tool_calls=calls or []))[0]


def call(name, **args):
    return {"name": name, "args": args, "result": None, "error": None}


# ---- tool_call --------------------------------------------------------------------------------------

def test_the_right_tool_with_the_right_arguments_passes():
    r = run({"type": "tool_call"}, [call("refund", order_id="A1", amount=19.99)],
            {"tool_calls": [{"name": "refund", "args": {"order_id": "A1", "amount": {"approx": 19.99, "tol": 0.01}}}]})
    assert r.passed and r.score == 1.0


def test_a_missing_tool_fails_and_is_named():
    r = run({"type": "tool_call"}, [], {"tool_calls": [{"name": "refund"}]})
    assert not r.passed and r.details["missing"] == ["refund"] and r.details["gave_up"] is True


def test_wrong_arguments_are_reported_as_bad_args_not_as_a_missing_tool():
    r = run({"type": "tool_call"}, [call("refund", order_id="B2")], {"tool_calls": [{"name": "refund", "args": {"order_id": "A1"}}]})
    assert not r.passed and r.details["missing"] == [] and r.details["bad_args"][0]["tool"] == "refund"
    assert "order_id" in r.rationale


def test_an_extra_call_is_allowed_unless_no_extra_is_set():
    calls = [call("lookup", id=1), call("refund", id=1)]
    exp = {"tool_calls": [{"name": "refund"}]}
    assert run({"type": "tool_call"}, calls, exp).passed
    r = run({"type": "tool_call", "no_extra": True}, calls, exp)
    assert not r.passed and r.details["extra"] == ["lookup"]


def test_a_forbidden_tool_fails_even_when_everything_expected_was_done():
    r = run({"type": "tool_call", "forbidden": ["delete_account"]}, [call("refund"), call("delete_account")],
            {"tool_calls": [{"name": "refund"}]})
    assert not r.passed and r.details["forbidden_called"] == ["delete_account"]


def test_an_empty_expected_list_means_no_tool_may_be_called():
    assert run({"type": "tool_call"}, [], {"tool_calls": []}).passed
    r = run({"type": "tool_call"}, [call("refund")], {"tool_calls": []})
    assert not r.passed and r.details["extra"] == ["refund"]


def test_two_expected_calls_need_two_different_real_calls():
    exp = {"tool_calls": [{"name": "search"}, {"name": "search"}]}
    assert not run({"type": "tool_call"}, [call("search", q="a")], exp).passed
    assert run({"type": "tool_call"}, [call("search", q="a"), call("search", q="b")], exp).passed


def test_a_loose_expectation_listed_first_does_not_steal_the_call_a_strict_one_needs():
    """Greedy matching would give the only search(q=a) call to the loose expectation and fail the strict one."""
    exp = {"tool_calls": [{"name": "search"}, {"name": "search", "args": {"q": "a"}}]}
    assert run({"type": "tool_call"}, [call("search", q="a"), call("search", q="b")], exp).passed


def test_fail_on_error_counts_a_tool_that_returned_an_error():
    calls = [{"name": "refund", "args": {}, "result": None, "error": "denied"}]
    exp = {"tool_calls": [{"name": "refund"}]}
    assert run({"type": "tool_call"}, calls, exp).passed
    assert not run({"type": "tool_call", "fail_on_error": True}, calls, exp).passed


def test_the_case_must_say_what_it_expects():
    r = run({"type": "tool_call"}, [call("x")], {})
    assert not r.passed and r.details["error"] is True


def test_expected_calls_can_be_given_in_the_spec_too():
    assert run({"type": "tool_call", "calls": [{"name": "x"}]}, [call("x")]).passed


# ---- trajectory -------------------------------------------------------------------------------------

def names(*n):
    return [call(x) for x in n]


@pytest.mark.parametrize("order,want,got,ok", [
    ("exact", ["a", "b"], ["a", "b"], True), ("exact", ["a", "b"], ["a", "x", "b"], False),
    ("in_order", ["a", "b"], ["a", "x", "b"], True), ("in_order", ["a", "b"], ["b", "a"], False),
    ("in_order", ["a", "a"], ["a", "b"], False), ("in_order", ["a", "a"], ["a", "b", "a"], True),
    ("any_order", ["a", "b"], ["b", "a"], True), ("any_order", ["a", "b"], ["a", "b", "c"], False),
    ("any_order", ["a", "a"], ["a", "b"], False),
    ("contains", ["a", "b"], ["c", "b", "a"], True), ("contains", ["a", "a"], ["a", "b"], False),
])
def test_trajectory_orders(order, want, got, ok):
    assert run({"type": "trajectory", "order": order}, names(*got), {"trajectory": want}).passed is ok


def test_in_order_is_the_default():
    assert run({"type": "trajectory"}, names("a", "x", "b"), {"trajectory": ["a", "b"]}).passed


def test_too_many_steps_fail():
    r = run({"type": "trajectory", "max_steps": 2}, names("a", "b", "c"))
    assert not r.passed and "maximum is 2" in r.rationale


def test_repeating_the_same_call_is_a_loop_but_different_arguments_are_not():
    assert run({"type": "trajectory"}, [call("search", q="a")] * 3).details["looped"] is True
    assert run({"type": "trajectory"}, [call("search", q=str(i)) for i in range(5)]).passed
    assert run({"type": "trajectory", "max_repeats": 3}, [call("search", q="a")] * 3).passed


def test_a_loop_is_found_regardless_of_argument_order():
    a = {"name": "t", "args": {"x": 1, "y": 2}}
    b = {"name": "t", "args": {"y": 2, "x": 1}}
    assert run({"type": "trajectory"}, [a, b, a]).details["loops"] == ["t"]


def test_no_calls_when_some_were_expected_is_giving_up():
    r = run({"type": "trajectory"}, [], {"trajectory": ["a"]})
    assert not r.passed and r.details["gave_up"] is True and r.score == 0.0


def test_partial_progress_gets_partial_score():
    r = run({"type": "trajectory", "order": "exact"}, names("a"), {"trajectory": ["a", "b"]})
    assert not r.passed and r.score == 0.5


def test_an_unknown_order_is_an_evaluator_error():
    assert run({"type": "trajectory", "order": "sideways"}).details["error"] is True


# ---- task_success -----------------------------------------------------------------------------------

def test_final_state_checks_tool_counts_arguments_results_and_text():
    calls = [{"name": "refund", "args": {"amount": 19.99}, "result": {"status": "ok"}, "error": None}]
    checks = [{"path": "tools.refund.count", "equals": 1}, {"path": "tools.refund.last.args.amount", "approx": 19.99, "tol": 0.01},
              {"path": "tools.refund.last.result.status", "equals": "ok"}, {"path": "output", "regex": "(?i)refund"},
              {"path": "tools.delete_account.count", "equals": 0}]
    assert run({"type": "task_success", "checks": checks}, calls, output="Refund issued").passed


def test_a_failed_check_names_the_path_and_the_reason():
    r = run({"type": "task_success", "checks": [{"path": "tools.refund.count", "equals": 1}]}, [])
    assert not r.passed and "tools.refund.count" in r.rationale and "expected 1, got 0" in r.rationale


def test_expected_final_state_is_a_shorthand_for_equals():
    calls = [{"name": "refund", "args": {}, "result": None, "error": None}]
    assert run({"type": "task_success"}, calls, {"final_state": {"tools.refund.count": 1}}).passed
    assert not run({"type": "task_success"}, calls, {"final_state": {"tools.refund.count": 2}}).passed


def test_a_path_that_does_not_exist_fails_instead_of_passing_silently():
    r = run({"type": "task_success", "checks": [{"path": "tools.refund.last.args.amount", "equals": 1}]}, [])
    assert not r.passed and "path not found" in r.rationale


def test_partial_checks_give_partial_score_and_malformed_checks_are_errors():
    r = run({"type": "task_success", "checks": [{"path": "output", "equals": "done"}, {"path": "output", "equals": "x"}]})
    assert not r.passed and r.score == 0.5
    assert run({"type": "task_success", "checks": [{"path": "output"}]}).details["error"] is True
    assert run({"type": "task_success"}).details["error"] is True


# ---- Load-time validation ---------------------------------------------------------------------------

def case(**kw):
    return CaseSpec(id="a", input=kw.pop("input", {"prompt": "hi"}), evaluation=kw.pop("evaluation", {"evaluators": [{"type": "tool_call"}]}), **kw)


@pytest.mark.parametrize("kwargs,needle", [
    ({"expected": {"tool_calls": "refund"}}, "must be a list"),
    ({"expected": {"tool_calls": [{"args": {}}]}}, "needs a 'name'"),
    ({"expected": {"tool_calls": [{"name": "x", "args": {"a": {"regex": "("}}}]}}, "args.a"),
    ({"expected": {"trajectory": "a"}}, "list of tool names"),
    ({"expected": {"final_state": []}}, "object"),
    ({"input": {"messages": []}}, "non-empty"),
    ({"input": {"messages": [{"role": "robot", "content": "x"}]}}, "messages[0]"),
    ({"evaluation": {"evaluators": [{"type": "task_success", "checks": [{"path": "output"}]}]}}, "no condition"),
])
def test_a_malformed_case_is_rejected_when_the_dataset_loads(kwargs, needle):
    with pytest.raises(DatasetError, match=needle.replace("[", r"\[").replace("]", r"\]")):
        validate([case(**kwargs)])


def test_a_good_agent_case_loads():
    validate([case(input={"messages": [{"role": "user", "content": "refund A1"}]},
                   expected={"tool_calls": [{"name": "refund", "args": {"id": {"type": "string"}}}], "trajectory": ["refund"]})])


def test_a_conversation_is_shown_to_a_plain_model_as_a_transcript():
    from trustbench.datasets import render_prompt
    text = render_prompt({"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]})
    assert text == "User: hi\nAssistant: hello"
