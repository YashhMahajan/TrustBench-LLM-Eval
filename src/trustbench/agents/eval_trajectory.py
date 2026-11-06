"""`trajectory`: did the app take the right steps, in a sensible order, without looping?

expected.trajectory: ["lookup_order", "refund"]    tool names, in the order we expect
spec:
  order: exact | in_order | any_order | contains    (default in_order)
      exact      the app called exactly these tools in this order
      in_order   these tools appear in this order; other calls may sit between them
      any_order  the same tools, in any order, and no others
      contains   these tools appear in any order; other calls are allowed
  max_steps: 6        most tool calls allowed (counts every call)
  max_repeats: 2      most times the same tool may be called with the same arguments (default 2). More is a loop.
"""

from collections import Counter

from ..evaluators import Ctx, Outcome, register

ORDERS = ("exact", "in_order", "any_order", "contains")


def is_subsequence(want: list, got: list) -> bool:
    it = iter(got)
    return all(any(x == y for y in it) for x in want)


def find_loops(calls: list[dict], max_repeats: int) -> list[str]:
    """Tools that were called with the same arguments more than `max_repeats` times."""
    seen = Counter((c.get("name"), repr(sorted((c.get("args") or {}).items(), key=lambda kv: kv[0]))) for c in calls)
    return sorted({name for (name, _), n in seen.items() if n > max_repeats})


@register("trajectory")
def trajectory(ctx: Ctx) -> Outcome:
    spec = ctx.spec
    order = spec.get("order", "in_order")
    if order not in ORDERS:
        return Outcome("trajectory", "?", False, 0.0, f"order must be one of {ORDERS}.", {"error": True})
    want = spec.get("steps", ctx.expected.get("trajectory"))
    got = [c.get("name") for c in ctx.tool_calls]
    problems = []
    if want is not None:
        ok = {"exact": got == want, "in_order": is_subsequence(want, got),
              "any_order": sorted(got) == sorted(want), "contains": not (Counter(want) - Counter(got))}[order]
        if not ok:
            problems.append(f"expected {order} {want}, got {got}")
    if "max_steps" in spec and len(got) > spec["max_steps"]:
        problems.append(f"{len(got)} tool calls, maximum is {spec['max_steps']}")
    loops = find_loops(ctx.tool_calls, spec.get("max_repeats", 2))
    if loops:
        problems.append(f"looped on {loops}")
    details = {"called": got, "looped": bool(loops), "loops": loops, "gave_up": bool(want) and not got}
    passed = not problems
    score = 1.0 if passed else (sum(1 for x in (want or []) if x in got) / len(want) if want else 0.0)
    return Outcome("trajectory", "1", passed, score, "The steps are right." if passed else "; ".join(problems), details)
