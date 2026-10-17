"""Review of proposed test cases: approve, reject, correct, export."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .datasets import CaseSpec, import_dataset, validate
from .db import ProposedCase, utcnow


class ReviewError(ValueError):
    pass


def list_proposals(factory: sessionmaker, status: str | None = None, dataset: str | None = None) -> list[ProposedCase]:
    with factory() as s:
        q = select(ProposedCase).order_by(ProposedCase.id)
        if status:
            q = q.where(ProposedCase.status == status)
        if dataset:
            q = q.where(ProposedCase.dataset_name == dataset)
        return list(s.scalars(q))


def _get(s, pid: int) -> ProposedCase:
    p = s.get(ProposedCase, pid)
    if p is None:
        raise ReviewError(f"No proposal {pid}.")
    return p


def approve(factory: sessionmaker, ids: list[int], confirm_unverified: bool = False, note: str = "") -> int:
    """Approve proposals. A case whose answer a model wrote needs confirm_unverified, so a person has checked it."""
    with factory.begin() as s:
        rows = [_get(s, i) for i in ids]
        unverified = [p.id for p in rows if not p.expected_verified]
        if unverified and not confirm_unverified:
            raise ReviewError(f"Proposal(s) {', '.join(map(str, unverified))} have an answer written by a model. "
                              "Check the answer, then approve with --checked-answers (or correct it with 'proposals edit').")
        for p in rows:
            p.status, p.note, p.reviewed_at = "approved", note or p.note, utcnow()
    return len(ids)


def reject(factory: sessionmaker, ids: list[int], note: str = "") -> int:
    with factory.begin() as s:
        for pid in ids:
            p = _get(s, pid)
            p.status, p.note, p.reviewed_at = "rejected", note, utcnow()
    return len(ids)


def edit_answer(factory: sessionmaker, pid: int, answer: str) -> None:
    """Correct the answer of a proposal. A person wrote it now, so it counts as verified."""
    with factory.begin() as s:
        p = _get(s, pid)
        spec = dict(p.spec)
        spec["expected"] = {"required_facts": [answer]}
        spec["evaluation"] = {"evaluators": [{"type": "contains", "values": [answer]}]}
        validate([CaseSpec(**spec)])
        p.spec, p.expected_verified = spec, True


def export_approved(factory: sessionmaker, dataset: str, out: Path, base: list[CaseSpec] | None = None) -> tuple[int, int]:
    """Write the approved cases (after the base cases, if given) and store them as a dataset version.

    Returns (approved cases, dataset version id). A new approval gives a new version.
    """
    approved = [CaseSpec(**p.spec) for p in list_proposals(factory, "approved", dataset)]
    if not approved:
        raise ReviewError(f"No approved proposals for '{dataset}'.")
    cases = [*(base or []), *approved]
    validate(cases)
    with out.open("w") as f:
        for c in cases:
            f.write(json.dumps(c.model_dump(), ensure_ascii=False) + "\n")
    with factory() as s:
        version = import_dataset(s, f"{dataset}-regression", cases)
    return len(approved), version.id
