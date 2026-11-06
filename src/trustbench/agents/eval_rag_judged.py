"""Judge-model checks for retrieval-augmented answers (RAG).

The retrieved documents come from the app's `retrieval` span, so you do not copy them into the dataset:

    with span("retrieval", "search_kb", docs=[doc.text for doc in hits]):     # docs: strings or {"id", "text"}
        ...

If the app records no retrieval span, input.context is used.

  rag_faithfulness       Every claim in the answer is supported by the retrieved documents.
                         spec: max_unsupported (default 0). A contradicted claim always fails.
  rag_answer_relevance   The answer addresses the question.            spec: min_score (0 to 3, default 2)
  rag_context_precision  The retrieved documents are useful.           spec: min (default 0.5)
  rag_context_recall     The documents contain what the answer needs.  spec: min (default 1.0)
                         Needs expected.required_facts (or expected.answer).

All four need a judge (--judge provider:model). Validate your judge first: trustbench validate-judge.
"""

from ..eval_grounded import check_grounded
from ..evaluators import Ctx, Outcome, register
from ..judge import Judge, Rubric, extract_json, grade
from ..verify import verify_claim

RELEVANCE = Rubric("answer_relevance", "v1", 3, (
    "Score how directly the answer addresses the question that was asked.\n"
    "0 = off topic or ignores the question\n"
    "1 = touches the topic but does not answer\n"
    "2 = answers, with irrelevant or missing parts\n"
    "3 = answers the question directly"))


def retrieved_docs(ctx: Ctx) -> list[str]:
    docs: list[str] = []
    for sp in ctx.spans:
        if sp.get("kind") == "retrieval":
            for d in (sp.get("data") or {}).get("docs", []):
                docs.append(d if isinstance(d, str) else str(d.get("text", "")))
    if not docs and ctx.input.get("context"):
        docs = [ctx.input["context"]]
    return [d for d in docs if d.strip()]


def question_of(ctx: Ctx) -> str:
    return ctx.input.get("question") or ctx.input.get("prompt") or ""


def _setup_error(name: str, ctx: Ctx, need_docs: bool = True) -> Outcome | None:
    if ctx.judge is None:
        return Outcome(name, "?", False, 0.0, "No judge is set. Use --judge provider:model.", {"error": True})
    if need_docs and not retrieved_docs(ctx):
        return Outcome(name, "?", False, 0.0,
                       "No retrieved documents. Record a retrieval span with docs=[...] in your app, or set input.context.",
                       {"error": True})
    return None


@register("rag_faithfulness", needs_judge=True)
async def rag_faithfulness(ctx: Ctx) -> Outcome:
    if (err := _setup_error("rag_faithfulness", ctx)):
        return err
    return await check_grounded(ctx, "\n\n".join(retrieved_docs(ctx)), "rag_faithfulness")


@register("rag_answer_relevance", needs_judge=True)
async def rag_answer_relevance(ctx: Ctx) -> Outcome:
    if (err := _setup_error("rag_answer_relevance", ctx, need_docs=False)):
        return err
    g = await grade(ctx.judge, RELEVANCE, question_of(ctx), ctx.output)      # An unreadable reply raises: an evaluator error.
    ok = g.score >= ctx.spec.get("min_score", 2)
    return Outcome("rag_answer_relevance", RELEVANCE.id, ok, g.normalized, g.rationale,
                   {"judge_score": g.score, "max_score": g.max_score})


async def _is_useful(judge: Judge, question: str, doc: str) -> bool | None:
    prompt = (f"<question>\n{question}\n</question>\n\n<document>\n{doc}\n</document>\n\n"
              "Does the document contain information that helps answer the question? "
              'Reply as JSON: {"useful": true or false}')
    try:
        return bool(extract_json(await judge.ask(prompt))["useful"])
    except (ValueError, KeyError, TypeError):
        return None


@register("rag_context_precision", needs_judge=True)
async def rag_context_precision(ctx: Ctx) -> Outcome:
    if (err := _setup_error("rag_context_precision", ctx)):
        return err
    docs = retrieved_docs(ctx)
    verdicts = [await _is_useful(ctx.judge, question_of(ctx), d) for d in docs]
    judged = [v for v in verdicts if v is not None]
    if not judged:
        return Outcome("rag_context_precision", "1", False, 0.0, "No document could be judged.",
                       {"error": True, "human_review": True})
    precision = sum(judged) / len(judged)
    ok = precision >= ctx.spec.get("min", 0.5)
    return Outcome("rag_context_precision", "1", ok, precision,
                   f"{sum(judged)} of {len(judged)} retrieved documents are useful.",
                   {"useful": verdicts, "documents": len(docs)})


@register("rag_context_recall", needs_judge=True)
async def rag_context_recall(ctx: Ctx) -> Outcome:
    if (err := _setup_error("rag_context_recall", ctx)):
        return err
    facts = ctx.expected.get("required_facts") or ([ctx.expected["answer"]] if "answer" in ctx.expected else [])
    if not facts:
        return Outcome("rag_context_recall", "?", False, 0.0, "The test case has no required_facts or answer.", {"error": True})
    context = "\n\n".join(retrieved_docs(ctx))
    verdicts = [await verify_claim(ctx.judge, str(f), context) for f in facts]
    found = [v.label == "supported" for v in verdicts]
    recall = sum(found) / len(facts)
    ok = recall >= ctx.spec.get("min", 1.0)
    lost = [str(f) for f, hit in zip(facts, found) if not hit]
    return Outcome("rag_context_recall", "1", ok, recall,
                   "The retrieved documents contain every required fact." if not lost else f"Not in the documents: {lost}",
                   {"missing_facts": lost})
