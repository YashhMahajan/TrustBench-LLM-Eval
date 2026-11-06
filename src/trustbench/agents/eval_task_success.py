"""`task_success`: is the end state right? Checked in code, so it needs no judge model.

The state the checks look at:
  output                          the final text
  tools.<name>.count              how many times the app called that tool (0 when never)
  tools.<name>.last.args.<key>    the arguments of its last call
  tools.<name>.last.result        what its last call returned (and .result.<key> if it is an object)
  tools.<name>.last.error         its error, or None

Checks, in the spec or as expected.final_state ({path: value} means "equals"):
  checks: [{"path": "tools.refund.count", "equals": 1},
           {"path": "tools.refund.last.args.amount", "approx": 19.99, "tol": 0.01},
           {"path": "output", "regex": "refund"},
           {"path": "tools.delete_account.count", "equals": 0}]
To grade open-ended success with a rubric, use the `llm_judge` evaluator.
"""

from ..evaluators import Ctx, Outcome, register
from .matchers import equal, matches, validate_matcher

MISSING = object()


def build_state(output: str, tool_calls: list[dict]) -> dict:
    tools: dict[str, dict] = {}
    for c in tool_calls:
        t = tools.setdefault(c.get("name"), {"count": 0, "last": None})
        t["count"] += 1
        t["last"] = {"args": c.get("args") or {}, "result": c.get("result"), "error": c.get("error")}
    return {"output": output, "tools": tools}


def resolve(state: dict, path: str):
    """Follow a dotted path. TrustBench reads a tool that the app never called as {count: 0, last: None}."""
    parts = path.split(".")
    cur = state
    for i, part in enumerate(parts):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif i == 1 and parts[0] == "tools":
            cur = {"count": 0, "last": None}
        else:
            return MISSING
    return cur


def checks_of(spec: dict, expected: dict) -> list[dict]:
    out = list(spec.get("checks", []))
    out += [{"path": p, "equals": v} for p, v in (expected.get("final_state") or {}).items()]
    return out


def validate_check(check: dict) -> None:
    if not isinstance(check, dict) or not isinstance(check.get("path"), str):
        raise ValueError("Each check needs a 'path'.")
    rest = {k: v for k, v in check.items() if k != "path"}
    if "equals" in rest:
        if len(rest) != 1:
            raise ValueError("A check with 'equals' takes nothing else.")
        return
    if not rest:
        raise ValueError(f"Check '{check['path']}' has no condition.")
    validate_matcher(rest)


@register("task_success")
def task_success(ctx: Ctx) -> Outcome:
    checks = checks_of(ctx.spec, ctx.expected)
    if not checks:
        return Outcome("task_success", "?", False, 0.0, "No checks and no expected.final_state.", {"error": True})
    try:
        for c in checks:
            validate_check(c)
    except ValueError as e:
        return Outcome("task_success", "?", False, 0.0, f"Bad check: {e}", {"error": True})
    state = build_state(ctx.output, ctx.tool_calls)
    failed = []
    for c in checks:
        value = resolve(state, c["path"])
        if value is MISSING:
            failed.append({"path": c["path"], "why": "path not found"})
            continue
        ok, why = (equal(c["equals"], value), f"expected {c['equals']!r}, got {value!r}") if "equals" in c else matches(
            {k: v for k, v in c.items() if k != "path"}, value)
        if not ok:
            failed.append({"path": c["path"], "why": why})
    passed = not failed
    return Outcome("task_success", "1", passed, 1 - len(failed) / len(checks),
                   "Every check holds." if passed else "; ".join(f"{f['path']}: {f['why']}" for f in failed),
                   {"failed": failed, "checks": len(checks)})
