"""Dataset checks. They find problems before a run spends money."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass

from .datasets import CaseSpec, render_prompt
from .evaluators import NEEDS_JUDGE

NEAR_DUPLICATE = 0.85  # Jaccard similarity of word triples.


@dataclass
class Issue:
    severity: str  # error | warning
    code: str
    case_ids: list[str]
    message: str


def _words(case: CaseSpec) -> list[str]:
    return re.sub(r"[^\w\s]", " ", render_prompt(case.input).lower()).split()


def _shingles(words: list[str], n: int = 3) -> set[tuple]:
    if len(words) < n:
        return {tuple(words)}
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _expected_key(case: CaseSpec) -> str:
    return json.dumps(case.expected, sort_keys=True)


def check_duplicates_and_conflicts(cases: list[CaseSpec]) -> list[Issue]:
    issues: list[Issue] = []
    groups: dict[str, list[CaseSpec]] = defaultdict(list)
    for c in cases:
        groups[" ".join(_words(c))].append(c)
    exact_pairs = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        ids = [c.id for c in group]
        exact_pairs.update((a, b) for a in ids for b in ids if a < b)
        if len({_expected_key(c) for c in group}) > 1:
            issues.append(Issue("error", "conflicting_answers", ids,
                                "The same input has different expected answers."))
        else:
            issues.append(Issue("warning", "duplicate", ids, "The same input appears more than once."))

    # Near duplicates. An index on triples limits the pairs we compare.
    sh = {c.id: _shingles(_words(c)) for c in cases}
    index: dict[tuple, list[str]] = defaultdict(list)
    for cid, s in sh.items():
        for t in s:
            index[t].append(cid)
    seen = set()
    for cid, s in sh.items():
        shared: dict[str, int] = defaultdict(int)
        for t in s:
            for other in index[t]:
                if other > cid:
                    shared[other] += 1
        for other, n in shared.items():
            pair = (cid, other)
            if pair in exact_pairs or pair in seen:
                continue
            jac = n / (len(s) + len(sh[other]) - n)
            if jac >= NEAR_DUPLICATE:
                seen.add(pair)
                issues.append(Issue("warning", "near_duplicate", [cid, other],
                                    f"The inputs are {jac:.0%} the same."))
    return issues


# What each evaluator needs from the test case.
def _missing(case: CaseSpec, spec: dict) -> str | None:
    t, e = spec["type"], case.expected
    has_answer = "answer" in e or bool(e.get("answers"))
    if t in ("exact_match", "token_f1") and not has_answer:
        return "needs expected.answer"
    if t == "contains" and not (spec.get("values") or e.get("required_facts")):
        return "needs expected.required_facts or spec.values"
    if t == "numeric" and "number" not in e:
        return "needs expected.number"
    if t == "abstain" and "abstain" not in e:
        return "needs expected.abstain"
    if t == "json_schema" and not (spec.get("schema") or e.get("schema")):
        return "needs a schema"
    if t == "grounded_claims" and not (case.input.get("context") or e.get("evidence")):
        return "needs input.context or expected.evidence"
    return None


def check_references(cases: list[CaseSpec]) -> list[Issue]:
    issues = []
    for c in cases:
        for spec in c.evaluation.get("evaluators") or [{"type": "exact_match"}]:
            problem = _missing(c, spec)
            if problem:
                issues.append(Issue("error", "missing_reference", [c.id], f"{spec['type']} {problem}."))
            if spec["type"] == "llm_judge" and spec.get("use_reference", True) and not (
                    "answer" in c.expected or c.expected.get("answers")):
                issues.append(Issue("warning", "judge_without_reference", [c.id],
                                    "llm_judge has no reference answer. It will grade from its own knowledge."))
    return issues


def check(cases: list[CaseSpec]) -> list[Issue]:
    return check_references(cases) + check_duplicates_and_conflicts(cases)
