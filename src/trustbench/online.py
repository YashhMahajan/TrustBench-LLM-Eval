"""Online evaluation: check a sample of real traffic while it happens.

We have no reference answer for real traffic. So we allow only evaluators that work without one:
  rule, json_schema, contains   measured: a rule finds the problem, so no guess enters.
  grounded_claims, llm_judge    estimated: a judge model guesses, and a judge can be wrong.
Every result says which kind it is. A dashboard must not mix the two.

Privacy: the redacted text waits in an ENCRYPTED task until a worker has handled it. The task is
emptied when it is done. The trace keeps text only if the workspace turned store_content on.
"""

import asyncio
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from . import crypto
from .db import ProductionTrace, TraceEvaluation, Workspace
from .dataprotect import policy_of
from .evaluators import evaluate
from .jobs import Handler, Job, make_queue
from .redact import redact

ONLINE_EVALUATORS = {"rule", "json_schema", "contains", "grounded_claims", "llm_judge"}
JUDGE_BASED = {"grounded_claims", "llm_judge"}
KIND_JOB = "evaluate_trace"


def check_specs(specs: list[dict], judge: str | None) -> str | None:
    """Return a problem text, or None when you can use the evaluator list."""
    for i, spec in enumerate(specs):
        kind = spec.get("type") if isinstance(spec, dict) else None
        if kind not in ONLINE_EVALUATORS:
            return f"online_evaluators[{i}]: '{kind}' cannot run without a reference answer. Use one of: {', '.join(sorted(ONLINE_EVALUATORS))}."
        if kind in JUDGE_BASED and not judge:
            return f"online_evaluators[{i}]: '{kind}' needs online_judge (provider:model)."
    if judge and ":" not in judge:
        return "online_judge must look like provider:model."
    return None


def enqueue_for_evaluation(factory: sessionmaker, workspace_id: str, payloads: dict[int, dict]) -> int:
    """One task for each sampled trace. The texts go into the task encrypted."""
    queue = make_queue(factory)
    for pk, texts in payloads.items():
        queue.enqueue(KIND_JOB, {"trace_pk": pk, "enc": crypto.encrypt_text(json.dumps(texts))}, workspace_id=workspace_id)
    return len(payloads)


def sanitize_details(details: dict) -> dict:
    """Keep numbers and flags. Drop the claim list: it quotes the answer."""
    return {k: v for k, v in details.items() if k != "claims"}


def run_online_evaluation(factory: sessionmaker, trace_pk: int, texts: dict, provider_factory=None) -> int:
    """Evaluate one trace and store the results. Safe to run twice. A second run replaces old results. Returns the number of results."""
    from .engine import make_judge
    from .providers import get_provider

    with factory() as s:
        row = s.get(ProductionTrace, trace_pk)
        if row is None:
            return 0                                   # Someone deleted the trace in the meantime.
        ws = s.get(Workspace, row.workspace_id)
        policy, workspace_id = policy_of(ws), row.workspace_id
    specs = []
    for spec in policy.online_evaluators:
        specs.append({**spec, "use_reference": False} if spec["type"] == "llm_judge" else spec)
    judge = make_judge(policy.online_judge, provider_factory or get_provider) if policy.online_judge and any(sp["type"] in JUDGE_BASED for sp in specs) else None
    inp = {"prompt": texts.get("input") or "", "question": texts.get("input") or "", "context": texts.get("context")}
    outcomes = asyncio.run(evaluate({"evaluators": specs}, inp, {}, texts.get("output") or "", judge)) if specs else []
    with factory.begin() as s:
        s.execute(delete(TraceEvaluation).where(TraceEvaluation.trace_pk == trace_pk))
        for o in outcomes:
            why = redact(o.rationale[:300], policy.extra_redactions)[0] if policy.redact else o.rationale[:300]
            s.add(TraceEvaluation(trace_pk=trace_pk, workspace_id=workspace_id, evaluator=o.evaluator, rubric_version=o.rubric_version,
                                  kind="estimated" if o.evaluator in JUDGE_BASED else "measured", passed=o.passed, score=o.score,
                                  rationale=why, details=sanitize_details(o.details)))
        s.get(ProductionTrace, trace_pk).eval_state = "done"
    return len(outcomes)


def evaluate_trace_handler(factory: sessionmaker, provider_factory=None) -> Handler:
    def handle(payload: dict, job: Job) -> None:
        try:
            texts = json.loads(crypto.decrypt_text(payload["enc"]))
            run_online_evaluation(factory, payload["trace_pk"], texts, provider_factory)
        except Exception:
            if job.attempts >= job.max_attempts:          # This was the last try. Show it on the trace.
                with factory.begin() as s:
                    row = s.get(ProductionTrace, payload.get("trace_pk"))
                    if row is not None:
                        row.eval_state = "error"
            raise
    return handle


def trace_evaluations(factory: sessionmaker, workspace_id: str, trace_pk: int) -> list[TraceEvaluation]:
    with factory() as s:
        return list(s.scalars(select(TraceEvaluation).where(TraceEvaluation.trace_pk == trace_pk, TraceEvaluation.workspace_id == workspace_id)
                              .order_by(TraceEvaluation.id)))
