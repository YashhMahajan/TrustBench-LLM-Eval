"""Turn failures into new test proposals.

A generator only PROPOSES. A proposal does not become a test until a person approves it,
because a generated case can carry a wrong expected answer. Two kinds of proposal:
  transform:NAME     the failing case under a meaning-preserving change. The expected result is
                     known, because the transform keeps it (expected_verified = true).
  model_variation    a judge model writes new cases that test the same skill. The answer comes
                     from a model, so it is not verified (expected_verified = false).
"""

import json
import random
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .datasets import CaseSpec, render_prompt, validate
from .db import EvaluationRun, ProposedCase, TestCase
from .judge import Judge, JudgeError, extract_json
from .report import case_rows
from .quality import check_duplicates_and_conflicts, check_pii
from .rundata import case_outcomes, load_run
from .transforms import TRANSFORMS, check_invariants


@dataclass
class ProposalReport:
    created: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    failing_cases: int = 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def cases_of_run(factory: sessionmaker, run_id: str) -> list[CaseSpec]:
    with factory() as s:
        run = s.get(EvaluationRun, run_id)
        rows = s.scalars(select(TestCase).where(TestCase.dataset_version_id == run.dataset_version_id)).all()
        return [CaseSpec(id=r.case_id, task=r.task, input=r.input, expected=r.expected,
                         evaluation=r.evaluation, metadata=r.meta) for r in rows]


def _spec_of(p: ProposedCase) -> CaseSpec:
    return CaseSpec(**p.spec)


async def _model_variations(judge: Judge, case: CaseSpec, wrong_output: str | None, n: int) -> list[CaseSpec]:
    prompt = (
        f"A model failed this test. Write {n} NEW test questions that test the same skill with different content. "
        "Each question must have one short, certain answer. Do not copy the original.\n\n"
        f"<test_question>\n{render_prompt(case.input)}\n</test_question>\n"
        f"<right_answer>\n{json.dumps(case.expected)}\n</right_answer>\n"
        f"<wrong_model_answer>\n{wrong_output or ''}\n</wrong_model_answer>\n\n"
        'Reply as JSON: {"cases": [{"prompt": "<the full question>", "answer": "<the short answer>"}]}'
    )
    try:
        items = extract_json(await judge.ask(prompt))["cases"]
    except (ValueError, KeyError, TypeError, JudgeError):
        return []
    out = []
    for i, item in enumerate(items[:n]):
        if not isinstance(item, dict) or not item.get("prompt") or not item.get("answer"):
            continue
        answer = str(item["answer"]).strip()
        out.append(CaseSpec(
            id=f"{case.id}~adv-model{i}", task=case.task, input={"prompt": str(item["prompt"]).strip()},
            expected={"required_facts": [answer]},
            evaluation={"evaluators": [{"type": "contains", "values": [answer]}]},
            metadata={**{k: v for k, v in case.metadata.items() if k != "critical"}, "source_case": case.id,
                      "transform": "model_variation", "generated": True}))
    return out


async def propose_from_failures(factory: sessionmaker, run_id: str, model: str | None = None,
                                judge: Judge | None = None, per_case: int = 2, seed: int = 0,
                                dataset_name: str | None = None) -> ProposalReport:
    rd = load_run(factory, run_id)
    records = rd.for_model(model)
    originals = {c.id: c for c in cases_of_run(factory, run_id)}
    model_name = rd.pick_model(model)
    outputs = {r["case_id"]: r["output"] for r in case_rows(factory, run_id) if r["model"] == model_name}
    outcomes = case_outcomes(records)
    failing = sorted(c for c, o in outcomes.items() if o is not None and o < 1.0 and c in originals
                     and not originals[c].metadata.get("source_case"))
    name = dataset_name or rd.dataset
    rep = ProposalReport(failing_cases=len(failing))

    with factory() as s:
        stored = [_spec_of(p) for p in s.scalars(select(ProposedCase).where(ProposedCase.dataset_name == name))]
    known = list(originals.values()) + stored
    candidates: list[tuple[CaseSpec, str, bool]] = []   # (case, method, expected_verified)
    for cid in failing:
        case = originals[cid]
        made = 0
        for tname, t in TRANSFORMS.items():
            if made >= per_case:
                break
            v = t.fn(case, random.Random(f"{seed}:{cid}:{tname}"))
            if v is None:
                continue
            problems = check_invariants(case, v)
            if problems:
                rep.skip(f"invalid: {problems[0]}")
                continue
            meta = {**case.metadata, "source_case": cid, "transform": tname, "generated": True}
            candidates.append((v.model_copy(update={"id": f"{cid}~adv-{tname}", "metadata": meta}), f"transform:{tname}", True))
            made += 1
        if judge:
            for v in await _model_variations(judge, case, outputs.get(cid), per_case):
                candidates.append((v, "model_variation", False))

    accepted: list[CaseSpec] = []
    with factory.begin() as s:
        for spec, method, verified in candidates:
            existing = {p.case_id for p in s.scalars(select(ProposedCase).where(ProposedCase.dataset_name == name))}
            if spec.id in existing:
                rep.skip("already proposed")
                continue
            try:
                validate([spec])
            except ValueError as e:
                rep.skip(f"invalid: {e}")
                continue
            if check_pii([spec]):
                rep.skip("possible personal data")
                continue
            # Compare with the dataset and the earlier proposals. Variants of the same source are allowed to look alike.
            clash = [i for i in check_duplicates_and_conflicts(known + accepted + [spec]) if spec.id in i.case_ids
                     and len(i.case_ids) > 1]
            if clash:
                rep.skip(f"duplicate of an existing case ({clash[0].code})")
                continue
            s.add(ProposedCase(dataset_name=name, case_id=spec.id, spec=spec.model_dump(), source_run_id=run_id,
                               source_case_id=spec.metadata.get("source_case"), method=method,
                               expected_verified=verified))
            accepted.append(spec)
            rep.created += 1
    return rep
