"""Evaluators. Each one returns a pass or fail with a reason.

An evaluator is a function that takes a Ctx and returns an Outcome.
It may be async, because some evaluators call a judge model.
"""

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Outcome:
    evaluator: str
    rubric_version: str
    passed: bool
    score: float
    rationale: str
    details: dict = field(default_factory=dict)


@dataclass
class Ctx:
    input: dict
    expected: dict
    output: str
    spec: dict
    judge: Any = None  # A trustbench.judge.Judge, when one is configured.


Evaluator = Callable[[Ctx], "Outcome | Awaitable[Outcome]"]
EVALUATORS: dict[str, Evaluator] = {}
# Evaluators in this set call a judge model.
NEEDS_JUDGE: set[str] = set()


def register(name: str, needs_judge: bool = False):
    def wrap(fn: Evaluator) -> Evaluator:
        EVALUATORS[name] = fn
        if needs_judge:
            NEEDS_JUDGE.add(name)
        return fn
    return wrap


def normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    return text.strip(" .!?,;:\"'")


@register("exact_match")
def exact_match(ctx: Ctx) -> Outcome:
    e = ctx.expected
    answers = e.get("answers") or ([e["answer"]] if "answer" in e else [])
    if not answers:
        return Outcome("exact_match", "1", False, 0.0, "No reference answer in the test case.")
    got = normalize(ctx.output)
    ok = any(got == normalize(str(a)) for a in answers)
    return Outcome("exact_match", "1", ok, float(ok), "Match." if ok else f"Expected one of {answers}.")


@register("contains")
def contains(ctx: Ctx) -> Outcome:
    values = ctx.spec.get("values") or ctx.expected.get("required_facts") or []
    if not values:
        return Outcome("contains", "1", False, 0.0, "No required values given.")
    text = ctx.output.lower()
    missing = [v for v in values if str(v).lower() not in text]
    score = 1 - len(missing) / len(values)
    return Outcome("contains", "1", not missing, score, "All found." if not missing else f"Missing: {missing}.")


@register("rule")
def rule(ctx: Ctx) -> Outcome:
    spec, output = ctx.spec, ctx.output
    failures = []
    words = len(output.split())
    if "max_words" in spec and words > spec["max_words"]:
        failures.append(f"{words} words, maximum is {spec['max_words']}")
    if "min_words" in spec and words < spec["min_words"]:
        failures.append(f"{words} words, minimum is {spec['min_words']}")
    if "regex" in spec and not re.search(spec["regex"], output):
        failures.append(f"no match for /{spec['regex']}/")
    for word in spec.get("forbidden", []):
        if word.lower() in output.lower():
            failures.append(f"contains forbidden text '{word}'")
    return Outcome("rule", "1", not failures, 0.0 if failures else 1.0, "; ".join(failures) or "All rules hold.")


async def evaluate(evaluation: dict, inp: dict, expected: dict, output: str, judge=None) -> list[Outcome]:
    """Run each evaluator in the test case. Default: exact match on the answer.

    An evaluator that raises an error gives a failed Outcome with details.error set.
    This way an evaluator problem is never mistaken for a model result.
    """
    specs = evaluation.get("evaluators") or [{"type": "exact_match"}]
    outcomes = []
    for spec in specs:
        name = spec["type"]
        try:
            result = EVALUATORS[name](Ctx(inp, expected, output, spec, judge))
            outcomes.append(await result if inspect.isawaitable(result) else result)
        except Exception as e:
            outcomes.append(Outcome(name, "?", False, 0.0, f"Evaluator error: {e}", {"error": True}))
    return outcomes


# These imports register more evaluators. They must stay at the end of the file.
from . import eval_judge, eval_numeric, eval_reference, eval_schema  # noqa: E402,F401
