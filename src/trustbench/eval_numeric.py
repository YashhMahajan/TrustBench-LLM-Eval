"""Compare a number in the output with the expected number."""

import re

from .evaluators import Ctx, Outcome, register

NUMBER = re.compile(r"-?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")


def numbers_in(text: str) -> list[float]:
    found = []
    for m in NUMBER.findall(text):
        try:
            found.append(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return found


@register("numeric")
def numeric(ctx: Ctx) -> Outcome:
    """spec: pick = 'last' (default) or 'first'; abs_tol, rel_tol (default 0)."""
    if "number" not in ctx.expected:
        return Outcome("numeric", "1", False, 0.0, "No expected number in the test case.", {"error": True})
    target = float(ctx.expected["number"])
    nums = numbers_in(ctx.output)
    if not nums:
        return Outcome("numeric", "1", False, 0.0, "No number found in the output.")
    got = nums[0] if ctx.spec.get("pick") == "first" else nums[-1]
    tol = max(ctx.spec.get("abs_tol", 0.0), ctx.spec.get("rel_tol", 0.0) * abs(target))
    ok = abs(got - target) <= tol
    return Outcome("numeric", "1", ok, float(ok), "Match." if ok else f"Got {got:g}, expected {target:g}.",
                   {"value": got})
