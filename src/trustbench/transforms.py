"""Changes to a test case that must not change the right answer.

If a model passes the original case and fails the changed case, the model is fragile.
A change is only useful if it keeps the meaning. So the code checks every variant (check_invariants). It drops a variant that fails the check and does not test it.
"""

import json
import random
import re
from dataclasses import dataclass
from typing import Callable

from .datasets import CaseSpec

TEXT_FIELDS = ("prompt", "question")

FILLER = [
    "The office is cleaned every evening.", "Visitors should use the main entrance.",
    "A newsletter is sent once a month.", "The car park has two floors.",
    "Lunch is served in the canteen.", "Printers are on the second floor.",
    "Meeting rooms can be booked online.", "The building has a bicycle store.",
    "Windows are cleaned twice a year.", "A plant is watered each Friday.",
]
FORMATS = [
    "Question: {q}\nAnswer:", "Please answer the following.\n\n{q}", "{q}\n\nThank you.",
    "### Task\n{q}\n### Response", "Here is my question. {q}",
]


def protected_words(case: CaseSpec) -> set[str]:
    """Words a transform must not touch: they decide what the right answer is."""
    text = json.dumps([case.expected, case.evaluation], ensure_ascii=False)
    return {w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)}


def quoted_words(text: str) -> set[str]:
    return {w.lower() for q in re.findall(r"'([^']+)'|\"([^\"]+)\"", text) for part in q for w in re.findall(r"[A-Za-z]+", part)}


def _edit(case: CaseSpec, fn: Callable[[str], str], context_fn: Callable[[str], str] | None = None) -> CaseSpec | None:
    inp = dict(case.input)
    changed = False
    for key in TEXT_FIELDS:
        if isinstance(inp.get(key), str):
            new = fn(inp[key])
            changed |= new != inp[key]
            inp[key] = new
    if context_fn and isinstance(inp.get("context"), str):
        new = context_fn(inp["context"])
        changed |= new != inp["context"]
        inp["context"] = new
    return case.model_copy(update={"input": inp}) if changed else None


def split_sentences(text: str) -> list[str]:
    return [" ".join(s.split()) for s in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if s.strip()]


# ---- The transforms. Each takes (case, rng) and returns a changed case or None. ----------

def whitespace(case: CaseSpec, rng: random.Random) -> CaseSpec | None:
    def messy(t: str) -> str:
        t = re.sub(r" ", lambda m: "  " if rng.random() < 0.3 else " ", t)
        return f"\n{t} \n"
    return _edit(case, messy, messy)


def formatting(case: CaseSpec, rng: random.Random) -> CaseSpec | None:
    template = rng.choice(FORMATS)
    return _edit(case, lambda t: template.format(q=t))


def typos(case: CaseSpec, rng: random.Random) -> CaseSpec | None:
    protect = protected_words(case)

    def noisy(text: str) -> str:
        keep = protect | quoted_words(text)

        spans = [m for m in re.finditer(r"\b[A-Za-z]{5,}\b", text) if m.group(0).lower() not in keep]
        chosen = [m for m in spans if rng.random() < 0.2] or (spans and [rng.choice(spans)])
        for m in sorted(chosen or [], key=lambda m: -m.start()):   # From the end, so the positions stay right.
            w = m.group(0)
            i = rng.randrange(1, len(w) - 2)
            text = text[:m.start()] + w[:i] + w[i + 1] + w[i] + w[i + 2:] + text[m.end():]
        return text
    # The transform leaves the context alone, so the evidence stays exact.
    return _edit(case, noisy)


def reorder_context(case: CaseSpec, rng: random.Random) -> CaseSpec | None:
    ctx = case.input.get("context")
    if not isinstance(ctx, str) or case.metadata.get("order_sensitive"):
        return None
    parts = split_sentences(ctx)
    if len(parts) < 3:
        return None
    shuffled = parts[:]
    for _ in range(10):
        rng.shuffle(shuffled)
        if shuffled != parts:
            break
    else:
        return None
    return _edit(case, lambda t: t, lambda c: "\n".join(shuffled) if "\n" in c else " ".join(shuffled))


def _facts(case: CaseSpec) -> list[str]:
    e = case.expected
    out = list(e.get("required_facts") or [])
    out += [str(e["answer"])] if "answer" in e else []
    return [f for f in out if f]


def _move_evidence(case: CaseSpec, rng: random.Random, where: str) -> CaseSpec | None:
    ctx = case.input.get("context")
    if not isinstance(ctx, str):
        return None
    parts = split_sentences(ctx)
    facts = [f.lower() for f in _facts(case)]
    idx = next((i for i, s in enumerate(parts) if any(f in s.lower() for f in facts)), None)
    if idx is None:
        return None
    evidence, others = parts[idx], parts[:idx] + parts[idx + 1:]
    fillers = rng.sample(FILLER, 5) + rng.sample(FILLER, 5)
    if where == "first":
        new = [evidence, *others]
    elif where == "last":
        new = [*others, evidence]
    else:  # buried: filler sentences on both sides.
        new = [*fillers[:5], *others, evidence, *fillers[5:]]
    if new == parts:
        return None
    return _edit(case, lambda t: t, lambda c: " ".join(new))


def evidence_first(case, rng): return _move_evidence(case, rng, "first")
def evidence_last(case, rng): return _move_evidence(case, rng, "last")
def evidence_buried(case, rng): return _move_evidence(case, rng, "buried")


@dataclass
class Transform:
    name: str
    description: str
    fn: Callable[[CaseSpec, random.Random], CaseSpec | None]


TRANSFORMS: dict[str, Transform] = {t.name: t for t in [
    Transform("whitespace", "Extra spaces and blank lines", whitespace),
    Transform("formatting", "A different wrapper around the question", formatting),
    Transform("typos", "Swapped letters in words that do not decide the answer", typos),
    Transform("reorder_context", "Sentences of the context in another order", reorder_context),
    Transform("evidence_first", "The sentence with the answer moved to the start", evidence_first),
    Transform("evidence_last", "The sentence with the answer moved to the end", evidence_last),
    Transform("evidence_buried", "The answer sentence hidden between unrelated sentences", evidence_buried),
]}


# ---- Checks ----------------------------------------------------------------------------

def _numbers(case: CaseSpec) -> list[str]:
    text = " ".join(str(v) for v in case.input.values() if isinstance(v, str))
    return sorted(re.findall(r"\d+(?:[.,]\d+)*", text))


def check_invariants(original: CaseSpec, variant: CaseSpec) -> list[str]:
    """Return the reasons a variant is not a fair copy of the original. An empty list means it is fair."""
    problems = []
    if variant.expected != original.expected or variant.evaluation != original.evaluation:
        problems.append("the expected result or the evaluators changed")
    if _numbers(original) != _numbers(variant):
        problems.append("the numbers in the input changed")
    ctx_o, ctx_v = original.input.get("context"), variant.input.get("context")
    if isinstance(ctx_o, str) and isinstance(ctx_v, str):
        if not {s.lower() for s in split_sentences(ctx_o)} <= {s.lower() for s in split_sentences(ctx_v)}:
            problems.append("a sentence of the context was lost or changed")
    squash = lambda t: " ".join(t.lower().split())
    for fact in _facts(original):
        if isinstance(ctx_o, str) and squash(fact) in squash(ctx_o) and not (isinstance(ctx_v, str) and squash(fact) in squash(ctx_v)):
            problems.append(f"the evidence '{fact}' is no longer in the context")
    quoted = quoted_words(" ".join(str(v) for v in original.input.values() if isinstance(v, str)))
    text_v = " ".join(str(v) for v in variant.input.values() if isinstance(v, str)).lower()
    for w in sorted(quoted):
        if w not in text_v:
            problems.append(f"the quoted word '{w}' was changed")
    return problems


@dataclass
class BuildReport:
    applied: dict[str, int]
    not_applicable: dict[str, int]
    rejected: dict[str, int]


def build_variants(cases: list[CaseSpec], names: list[str], seed: int = 0) -> tuple[list[CaseSpec], BuildReport]:
    unknown = [n for n in names if n not in TRANSFORMS]
    if unknown:
        raise ValueError(f"Unknown transform(s): {', '.join(unknown)}. Choose from: {', '.join(TRANSFORMS)}.")
    out: list[CaseSpec] = []
    rep = BuildReport({n: 0 for n in names}, {n: 0 for n in names}, {n: 0 for n in names})
    for case in cases:
        if case.metadata.get("source_case"):
            continue   # Never make a variant of a variant.
        for name in names:
            variant = TRANSFORMS[name].fn(case, random.Random(f"{seed}:{case.id}:{name}"))
            if variant is None:
                rep.not_applicable[name] += 1
            elif check_invariants(case, variant):
                rep.rejected[name] += 1
            else:
                meta = {**case.metadata, "source_case": case.id, "transform": name}
                out.append(variant.model_copy(update={"id": f"{case.id}~{name}", "metadata": meta}))
                rep.applied[name] += 1
    return out, rep
