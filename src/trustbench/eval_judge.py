"""Rubric scoring by a judge model."""

from .evaluators import Ctx, Outcome, register
from .judge import RUBRICS, grade


def question_of(inp: dict) -> str:
    return inp.get("prompt") or inp.get("question", "")


def reference_of(expected: dict) -> str | None:
    if "answer" in expected:
        return str(expected["answer"])
    if expected.get("answers"):
        return str(expected["answers"][0])
    return None


@register("llm_judge", needs_judge=True)
async def llm_judge(ctx: Ctx) -> Outcome:
    """spec: rubric (default 'correctness'), pass_at, repeats, use_reference (default true)."""
    if ctx.judge is None:
        return Outcome("llm_judge", "?", False, 0.0, "No judge is set. Use --judge provider:model.", {"error": True})
    rubric = RUBRICS[ctx.spec.get("rubric", "correctness")]
    pass_at = ctx.spec.get("pass_at", max(1, round(rubric.max_score * 2 / 3)))
    ref = reference_of(ctx.expected) if ctx.spec.get("use_reference", True) else None
    g = await grade(ctx.judge, rubric, question_of(ctx.input), ctx.output, ref,
                    ctx.input.get("context"), ctx.spec.get("repeats", 1))
    details = {"scores": g.scores, "raw_score": g.score, "max_score": g.max_score, "pass_at": pass_at,
               "judge_model": ctx.judge.model, "human_review": g.disagreement}
    return Outcome("llm_judge", g.rubric_id, g.score >= pass_at, g.normalized, g.rationale, details)
