"""Claim-level groundedness: every claim in the answer is checked against the evidence."""

from .claims import extract_claims, sentence_claims
from .evaluators import Ctx, Outcome, register
from .hallucination import claim_metrics
from .verify import verify_claims


@register("grounded_claims", needs_judge=True)
async def grounded_claims(ctx: Ctx) -> Outcome:
    """The evidence is input.context (or expected.evidence).

    spec: max_unsupported = largest allowed share of unsupported claims (default 0).
          claims = 'model' (default, a judge splits the answer) or 'sentences' (no model call).
    A contradicted claim always fails the case.
    """
    if ctx.judge is None:
        return Outcome("grounded_claims", "?", False, 0.0, "No judge is set. Use --judge provider:model.",
                       {"error": True})
    evidence = ctx.input.get("context") or ctx.expected.get("evidence")
    if not evidence:
        return Outcome("grounded_claims", "?", False, 0.0, "The test case has no evidence.", {"error": True})
    claims = (sentence_claims(ctx.output) if ctx.spec.get("claims") == "sentences"
              else await extract_claims(ctx.judge, ctx.output))
    verdicts = await verify_claims(ctx.judge, claims, evidence)
    m = claim_metrics(verdicts)
    details = {"metrics": m.to_dict(), "claims": [v.to_dict() for v in verdicts],
               "human_review": bool(m.needs_review) or m.assessed == 0 and m.total > 0}
    if m.total == 0:
        return Outcome("grounded_claims", "1", True, 1.0, "The answer has no factual claims.", details)
    if m.assessed == 0:
        return Outcome("grounded_claims", "1", False, 0.0, "No claim could be judged.", details)
    bad = [v for v in verdicts if v.label in ("contradicted", "insufficient")]
    passed = m.contradicted == 0 and m.unsupported_claim_rate <= ctx.spec.get("max_unsupported", 0.0)
    why = "All assessed claims are supported." if passed else "; ".join(
        f"{v.label}: {v.claim}" for v in bad)
    return Outcome("grounded_claims", "1", passed, m.evidence_coverage, why, details)
