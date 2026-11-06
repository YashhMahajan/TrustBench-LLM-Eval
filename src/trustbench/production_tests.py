"""From a failure in production to a test that keeps it fixed.

  1. flagged_traces      find traces that went wrong: an error, a bad rating, a failed online check
  2. promote             turn one into a PROPOSED test case (redacted text only)
  3. proposals approve   a person reviews it            (trustbench proposals approve)
  4. proposals export    the approved cases become a dataset version
  5. run + gate          the next release must pass the old failure

A case needs stored text, so the workspace must have store_content on. The text is already
redacted. If the text still has personal data, we refuse the case.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .datasets import CaseSpec, validate
from .db import ProductionTrace, ProposedCase, TraceEvaluation, Workspace
from .dataprotect import policy_of
from .online import JUDGE_BASED
from .quality import check_duplicates_and_conflicts, check_pii
from .traces import reveal


class PromoteError(ValueError):
    pass


@dataclass
class Flagged:
    trace_id: str
    name: str
    reasons: list[str]          # error, bad_feedback, check:rule (measured), ...
    has_content: bool


def flagged_traces(factory: sessionmaker, workspace_id: str, name: str | None = None, limit: int = 100) -> list[Flagged]:
    with factory() as s:
        q = select(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id)
        if name:
            q = q.where(ProductionTrace.name == name)
        out = []
        for row in s.scalars(q.order_by(ProductionTrace.started_at.desc())):
            reasons = []
            if row.status == "error":
                reasons.append("error")
            if row.feedback == -1:
                reasons.append("bad_feedback")
            for e in s.scalars(select(TraceEvaluation).where(TraceEvaluation.trace_pk == row.id, TraceEvaluation.passed.is_(False))):
                if not e.details.get("error"):                       # A broken evaluator is not a problem of the system.
                    reasons.append(f"check:{e.evaluator} ({e.kind})")
            if reasons:
                out.append(Flagged(row.trace_id, row.name, reasons, bool(row.input_enc)))
                if len(out) >= limit:
                    break
        return out


def promote(factory: sessionmaker, workspace_id: str, trace_id: str, answer: str | None = None, dataset: str | None = None) -> ProposedCase:
    with factory.begin() as s:
        row = s.scalar(select(ProductionTrace).where(ProductionTrace.workspace_id == workspace_id, ProductionTrace.trace_id == trace_id))
        if row is None:
            raise PromoteError("No such trace.")
        if not row.input_enc:
            raise PromoteError("This trace has no stored text, so it cannot become a test. Turn on store_content for the workspace "
                               "(trustbench workspace set NAME --store-content), or write the test case by hand.")
        ws = s.get(Workspace, workspace_id)
        policy = policy_of(ws)
        failed = [e for e in s.scalars(select(TraceEvaluation).where(TraceEvaluation.trace_pk == row.id, TraceEvaluation.passed.is_(False)))
                  if not e.details.get("error")]
        text = reveal(row)
        inp = {"context": text["context"], "question": text["input"]} if text["context"] else {"prompt": text["input"]}

        origin = [r for r, hit in (("error", row.status == "error"), ("bad_feedback", row.feedback == -1)) if hit]
        origin += [f"check:{e.evaluator}" for e in failed]
        specs = {sp["type"]: sp for sp in policy.online_evaluators}
        evaluators: list[dict] = []
        verified = True
        if answer:
            evaluators.append({"type": "contains", "values": [answer]})
            expected = {"required_facts": [answer]}
        else:
            expected = {}
            for e in failed:
                spec = specs.get(e.evaluator)
                if spec and spec not in evaluators:
                    evaluators.append({**spec, "use_reference": False} if e.evaluator == "llm_judge" else spec)
                    verified &= e.evaluator not in JUDGE_BASED     # A judge model only guessed that this was a failure.
            if not evaluators:
                raise PromoteError("There is no rule that says what is right for this case. Give the right answer (--answer), "
                                   "or add an online evaluator that catches this kind of failure.")
        spec = CaseSpec(id=f"prod-{trace_id[:20]}", task=row.name, input=inp, expected=expected, evaluation={"evaluators": evaluators},
                        metadata={"source": "production", "trace_id": trace_id, "origin": ", ".join(origin) or "manual", "generated": False})
        try:
            validate([spec])
        except ValueError as e:
            raise PromoteError(str(e)) from e
        if check_pii([spec]):
            raise PromoteError("Personal data is still in the text. The case is refused. Add a custom redaction pattern to the workspace.")
        name = dataset or f"{row.name}-production"
        if s.scalar(select(ProposedCase).where(ProposedCase.dataset_name == name, ProposedCase.case_id == spec.id)):
            raise PromoteError("This trace was already proposed as a test.")
        existing = [CaseSpec(**p.spec) for p in s.scalars(select(ProposedCase).where(ProposedCase.dataset_name == name))]
        clash = [i for i in check_duplicates_and_conflicts(existing + [spec]) if spec.id in i.case_ids and len(i.case_ids) > 1]
        if clash:
            raise PromoteError(f"A very similar test exists already ({clash[0].case_ids[0]}).")
        prop = ProposedCase(dataset_name=name, case_id=spec.id, spec=spec.model_dump(), source_run_id=None, source_case_id=None,
                            method="production", expected_verified=verified, workspace_id=workspace_id,
                            note=f"From production trace {trace_id}")
        s.add(prop)
        s.flush()
        return prop
