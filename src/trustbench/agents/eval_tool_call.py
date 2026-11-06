"""`tool_call`: did the app call the right tools with the right arguments?

expected.tool_calls: [{"name": "refund", "args": {"order_id": "A1", "amount": {"approx": 19.99, "tol": 0.01}}}]
  An empty list means: the app must call no tool at all.
spec:
  forbidden: ["delete_account"]   tools that must never be called
  no_extra:  true                 every call must be one the case expects (default false)
  fail_on_error: true             a call that returned an error fails the case (default false)

Each expected call needs a different real call (order does not matter here, see `trajectory`).
"""

from ..evaluators import Ctx, Outcome, register
from .matchers import match_args, validate_matcher


def validate_expected_calls(calls) -> None:
    """Called when a dataset loads, so a typo fails early and not in the middle of a run."""
    if not isinstance(calls, list):
        raise ValueError("expected.tool_calls must be a list.")
    for i, c in enumerate(calls):
        if not isinstance(c, dict) or not isinstance(c.get("name"), str):
            raise ValueError(f"expected.tool_calls[{i}] needs a 'name'.")
        if not isinstance(c.get("args", {}), dict):
            raise ValueError(f"expected.tool_calls[{i}].args must be an object.")
        for key, m in c.get("args", {}).items():
            try:
                validate_matcher(m)
            except ValueError as e:
                raise ValueError(f"expected.tool_calls[{i}].args.{key}: {e}") from e


def assign(expected: list[dict], actual: list[dict]) -> dict[int, tuple[int | None, list[str]]]:
    """Give each expected call its own real call. Returns {expected index: (actual index or None, problems)}.

    A search, not a greedy pass: a loose expectation listed first must not steal the call that a strict one needs.
    """
    fits = [[j for j, a in enumerate(actual) if a.get("name") == e["name"] and not match_args(e.get("args", {}), a.get("args") or {})]
            for e in expected]
    best: dict[int, int] = {}

    def solve(i: int, used: frozenset, chosen: dict) -> None:
        nonlocal best
        if i == len(expected):
            if len(chosen) > len(best):
                best = dict(chosen)
            return
        if len(best) == len(expected):
            return
        for j in fits[i]:
            if j not in used:
                solve(i + 1, used | {j}, {**chosen, i: j})
        solve(i + 1, used, chosen)                          # Leave this one unmatched.

    solve(0, frozenset(), {})
    out: dict[int, tuple[int | None, list[str]]] = {}
    free = [j for j in range(len(actual)) if j not in best.values()]
    for i, e in enumerate(expected):
        if i in best:
            out[i] = (best[i], [])
            continue
        same_name = [j for j in free if actual[j].get("name") == e["name"]]
        problems = match_args(e.get("args", {}), actual[same_name[0]].get("args") or {}) if same_name else [f"'{e['name']}' was not called"]
        out[i] = (None, problems)
    return out


@register("tool_call")
def tool_call(ctx: Ctx) -> Outcome:
    spec = ctx.spec
    expected = spec.get("calls", ctx.expected.get("tool_calls"))
    if expected is None and not spec.get("forbidden"):
        return Outcome("tool_call", "1", False, 0.0, "The test case has no expected.tool_calls.", {"error": True})
    expected = expected or []
    actual = ctx.tool_calls
    names = [a.get("name") for a in actual]
    result = assign(expected, actual)
    missing, bad_args, matched = [], [], set()
    for i, (j, problems) in result.items():
        if j is not None:
            matched.add(j)
        elif f"'{expected[i]['name']}' was not called" in problems:
            missing.append(expected[i]["name"])
        else:
            bad_args.append({"tool": expected[i]["name"], "problems": problems})
    forbidden = [n for n in names if n in set(spec.get("forbidden", []))]
    extra = [names[j] for j in range(len(actual)) if j not in matched] if spec.get("no_extra") or (
        "tool_calls" in ctx.expected and not expected) else []
    errored = [a["name"] for a in actual if a.get("error")] if spec.get("fail_on_error") else []
    details = {"missing": missing, "bad_args": bad_args, "forbidden_called": forbidden, "extra": extra,
               "errored": errored, "called": names, "gave_up": bool(expected) and not actual}
    why = []
    if missing:
        why.append(f"not called: {missing}")
    why += [f"{b['tool']}: {'; '.join(b['problems'])}" for b in bad_args]
    if forbidden:
        why.append(f"forbidden tool called: {forbidden}")
    if extra:
        why.append(f"unexpected calls: {extra}")
    if errored:
        why.append(f"calls that failed: {errored}")
    passed = not why
    score = (len(matched) / len(expected)) if expected else (1.0 if passed else 0.0)
    return Outcome("tool_call", "1", passed, score if passed or expected else 0.0,
                   "All expected tool calls were made." if passed else "; ".join(why), details)
