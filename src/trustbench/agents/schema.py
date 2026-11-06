"""Load-time checks for the agent fields of a test case, so a typo fails at once and not in the middle of a run."""

from .eval_task_success import validate_check
from .eval_tool_call import validate_expected_calls


def validate_agent_fields(inp: dict, expected: dict, evaluation: dict) -> None:
    """Raise ValueError with a clear message if messages, tool calls, trajectory or checks are malformed."""
    if "messages" in inp:
        msgs = inp["messages"]
        if not isinstance(msgs, list) or not msgs:
            raise ValueError("input.messages must be a non-empty list.")
        for i, m in enumerate(msgs):
            if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant", "tool") or not isinstance(m.get("content"), str):
                raise ValueError(f"input.messages[{i}] needs a role (system, user, assistant or tool) and text content.")
    if "tool_calls" in expected:
        validate_expected_calls(expected["tool_calls"])
    if "trajectory" in expected and not (isinstance(expected["trajectory"], list) and all(isinstance(x, str) for x in expected["trajectory"])):
        raise ValueError("expected.trajectory must be a list of tool names.")
    if "final_state" in expected and not isinstance(expected["final_state"], dict):
        raise ValueError("expected.final_state must be an object of {path: value}.")
    for spec in evaluation.get("evaluators", []):
        if spec.get("type") == "tool_call" and "calls" in spec:
            validate_expected_calls(spec["calls"])
        if spec.get("type") == "task_success":
            for check in spec.get("checks", []):
                validate_check(check)
