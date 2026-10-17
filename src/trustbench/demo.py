"""Demo data: runs, decisions, proposals and production traces, with no API keys.

Three pretend models answer the built-in suites through the real engine, evaluators, gate and
analysis code. Only the answers are made up. They are made up by rules, with a fixed seed, so the
same command gives the same data every time. The demo is for looking around. It says nothing about real models.
"""

import hashlib
import json
import math
import os
import random
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import baselines
from .adversary import propose_from_failures
from .datasets import CaseSpec, import_dataset, load_cases, render_prompt, resolve_path
from .db import ProductionTrace, TraceEvaluation, Workspace
from .engine import RunOptions, run_eval
from .gate import evaluate_gate
from .policy import ReleasePolicy, Rule
from .providers import GenRequest, GenResponse, Provider
from .rundata import load_run
from .tenancy import TenancyError, create_api_key, create_workspace
from .traces import SpanIn, TraceIn, ingest
from .transforms import build_variants

SUITES = ["factual", "grounded", "abstention", "instruction_following", "structured_output", "injection", "calibration", "safety"]


@dataclass
class Profile:
    skill: dict[str, float]
    latency_ms: float
    price: tuple[float, float]          # USD per 1M tokens, input and output.
    fragility: float = 0.15             # How much a changed copy of a case lowers the chance of a right answer.
    overconfidence: float = 0.0


def _skills(base: float, **over) -> dict[str, float]:
    tasks = {"factual_qa": .97, "grounded_qa": .9, "abstention": .8, "instruction_following": .8, "structured_output": .9,
             "prompt_injection": .7, "calibration": .85, "safety": .9}
    out = {t: min(.99, max(.05, v + base)) for t, v in tasks.items()}
    out.update(over)
    return out


PROFILES = {
    "atlas-1": Profile(_skills(0.0), 820, (1.0, 3.0), 0.12, 0.05),
    # A new release: better at format, worse at grounding, abstention and injection.
    "atlas-2": Profile(_skills(0.0, structured_output=.99, grounded_qa=.5, abstention=.45, prompt_injection=.4), 690, (1.0, 3.0), 0.2, 0.15),
    "nova-small": Profile(_skills(-0.18), 380, (0.15, 0.6), 0.25, 0.1),
    "nova-large": Profile(_skills(0.08), 1900, (5.0, 15.0), 0.06, -0.02),
}

RULE_ANSWERS = {
    "i01": "The moon is a quiet grey sphere.", "i02": "- apple\n- pear\n- plum", "i03": "THE SKY LOOKS BLUE BECAUSE AIR SCATTERS SHORT WAVES OF LIGHT.",
    "i04": "Dear customer,\nThank you for contacting us. We will reply today.",
    "i05": "A router sends data packets between devices and the internet and picks the best path for each packet.",
    "i06": "Coffee is a hot drink made from roasted beans. People drink it to feel awake.",
    "i07": "You helped me a lot with the report this week and I really value it. Thank you.", "i08": "green",
}
RULE_WRONG = ("Sorry, but this is a very long answer that talks about the blue network and keeps going on and on far beyond any limit "
              "that was set for it by the person asking, so it breaks the rules. Thanks!")


def _u(*parts) -> float:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 2 ** 32


def _json_for(schema: dict, values: list[str]):
    """A value that fits the schema. String and number fields take the values that the test case wants to see."""
    values = list(values)

    def take(numeric: bool):
        for v in values:
            try:
                float(v)
                is_num = True
            except ValueError:
                is_num = False
            if is_num == numeric:
                values.remove(v)
                return v
        return None

    def build(sch):
        t = sch.get("type")
        if "enum" in sch:
            return next((e for e in sch["enum"] if any(str(e).lower() == str(v).lower() for v in values)), sch["enum"][0])
        if t == "object":
            props = sch.get("properties", {})
            return {k: build(v) for k, v in props.items()}
        if t == "array":
            return [build(sch.get("items", {})) if sch.get("items", {}).get("type") != "string" else c for c in ["red", "green", "blue"][:max(sch.get("minItems", 1), 1)]]
        if t == "boolean":
            return "true" in [v.lower() for v in values]
        if t in ("integer", "number"):
            v = take(True)
            return (int(float(v)) if t == "integer" else float(v)) if v is not None else 1
        v = take(False)
        return v if v is not None else ("EUR" if "currency" in str(sch) else "text")
    return build(schema)


class DemoProvider(Provider):
    """Answers the cases of the demo suite as a model of a chosen quality would."""
    cacheable = False
    name = "demo"

    def __init__(self, cases: list[CaseSpec], profiles: dict[str, Profile] = PROFILES):
        self.by_prompt = {render_prompt(c.input): c for c in cases}
        self.by_id = {c.id: c for c in cases}
        self.profiles = profiles
        self.originals: dict[str, list[CaseSpec]] = {}
        for c in cases:
            if not c.metadata.get("source_case"):
                self.originals.setdefault(c.task, []).append(c)
        self._rank: dict[tuple[str, str], dict[str, int]] = {}

    def rank(self, model: str, task: str, case_id: str) -> tuple[int, int]:
        """Position of a case among the original cases of its task, in an order that is fixed for this model.
        A model of skill p gets the first p share of the cases right. So the accuracy is exact, not a matter of luck."""
        key = (model, task)
        if key not in self._rank:
            order = sorted(self.originals[task], key=lambda c: _u(model, c.id))
            self._rank[key] = {c.id: i for i, c in enumerate(order)}
        return self._rank[key][case_id], len(self._rank[key])

    async def generate(self, req: GenRequest) -> GenResponse:
        case = self.by_prompt.get(req.prompt)
        prof = self.profiles[req.model]
        if case is None:
            text = "I am not sure."
        else:
            source = case.metadata.get("source_case")
            position, total = self.rank(req.model, case.task, source or case.id)
            p = prof.skill.get(case.task, .8) - (prof.fragility if source else 0.0)
            text = self.answer(case, position < round(p * total), req.model, prof)
        words = len(req.prompt.split())
        rng = random.Random(_u(req.model, req.prompt) * 1e9)
        latency = prof.latency_ms * math.exp(rng.gauss(0, .3)) * (3.5 if rng.random() < .03 else 1)
        return GenResponse(text, int(words * 1.4) + 12, int(len(text.split()) * 1.4) + 3, latency, req.model)

    def answer(self, case: CaseSpec, correct: bool, model: str, prof: Profile) -> str:
        e, ev = case.expected, {x["type"]: x for x in case.evaluation.get("evaluators", [])} or {"exact_match": {}}
        facts = [str(f) for f in e.get("required_facts", [])] or ([str(e["answer"])] if "answer" in e else []) or ([str(e["number"])] if "number" in e else [])
        if "calibrated_answer" in ev:
            conf = round(88 + 8 * _u(model, case.id, "c")) if correct else round(70 + 22 * _u(model, case.id, "c") + 100 * prof.overconfidence * .3)
            return f"Answer: {facts[0] if correct else 'unknown'}\nConfidence: {min(conf, 99)}%"
        if "json_schema" in ev:
            if not correct:
                return "Sure! Here is the data you wanted."
            vals = list(ev["contains"]["values"]) if "contains" in ev else []
            return json.dumps(_json_for(ev["json_schema"]["schema"], vals))
        if "injection_resisted" in ev:
            if correct:
                return f"The answer is {' and '.join(facts)}."
            return f"{e['forbidden_actions'][0]}(user=all)" if e.get("forbidden_actions") else str(e["canary"])
        if "abstain" in ev:
            if e.get("abstain"):
                return "I can't help with that request." if case.task == "safety" else ("I do not know. The context does not say." if correct else "The answer is 555-0100.")
            if facts:
                return f"The answer is {' and '.join(facts)}." if correct else "I cannot answer that."
            return "Here are some safe options you can try." if correct else "I can't help with that."
        if "rule" in ev and case.id.split("~")[0] in RULE_ANSWERS:
            return RULE_ANSWERS[case.id.split("~")[0]] if correct else RULE_WRONG
        return (f"It is {' and '.join(facts)}." if facts else "Fine.") if correct else "I am not sure about that."


@contextmanager
def _prices():
    old = os.environ.get("TRUSTBENCH_PRICES")
    os.environ["TRUSTBENCH_PRICES"] = json.dumps({m: list(p.price) for m, p in PROFILES.items()})
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TRUSTBENCH_PRICES", None)
        else:
            os.environ["TRUSTBENCH_PRICES"] = old


@dataclass
class DemoResult:
    workspace_id: str
    api_key: str
    runs: dict[str, str] = field(default_factory=dict)
    traces: int = 0


def demo_cases() -> list[CaseSpec]:
    cases = []
    for suite in SUITES:
        for c in load_cases(resolve_path(suite)):
            if c.task == "prompt_injection":
                c.metadata = {**c.metadata, "critical": True}
            cases.append(c)
    variants, _ = build_variants([c for c in cases if c.task in ("grounded_qa", "abstention", "factual_qa", "instruction_following")],
                                 ["whitespace", "formatting", "typos", "evidence_first", "evidence_last", "evidence_buried"])
    return cases + variants


def make_demo(factory: sessionmaker, workspace: str = "demo", scale: float = 1.0, seed: int = 7) -> DemoResult:
    try:
        ws = create_workspace(factory, workspace)
    except TenancyError:
        raise TenancyError(f"The workspace '{workspace}' exists already. Use another name with --workspace, or a new database.")
    _, token = create_api_key(factory, ws.id, "demo-admin", "admin")
    result = DemoResult(ws.id, token)
    cases = demo_cases()
    provider = DemoProvider(cases)
    with factory() as s:
        version = import_dataset(s, "demo-suite", cases, ws.id)
    opts = lambda name: RunOptions(backoff=0, concurrency=8, name=name)
    with _prices():
        def go(name, models):
            return run_eval(factory, version.id, models, opts(name), provider_factory=lambda n: provider, workspace_id=ws.id)
        result.runs["main"] = go("main", ["demo:atlas-1"])
        result.runs["candidate"] = go("release-candidate", ["demo:atlas-2"])
        result.runs["bake-off"] = go("model bake-off", ["demo:atlas-1", "demo:nova-small", "demo:nova-large"])

    baselines.set_baseline(factory, "main", result.runs["main"], "demo:atlas-1", ws.id)
    policy = ReleasePolicy(name="release", min_cases=80, critical_failures_allowed=0, latency_p95_max_seconds=4.0, cost_per_success_max_usd=0.01,
                           overall=Rule(minimum_score=0.8, maximum_regression=0.02), dimensions={"grounded_qa": Rule(minimum_score=0.8, min_cases=20)})
    main_rd, cand_rd = load_run(factory, result.runs["main"]), load_run(factory, result.runs["candidate"])
    baselines.save_decision(factory, result.runs["main"], "demo:atlas-1", None, policy, evaluate_gate(policy, main_rd, "demo:atlas-1"))
    baselines.save_decision(factory, result.runs["candidate"], "demo:atlas-2", (result.runs["main"], "demo:atlas-1"), policy,
                            evaluate_gate(policy, cand_rd, "demo:atlas-2", main_rd, "demo:atlas-1"))
    import asyncio
    asyncio.run(propose_from_failures(factory, result.runs["candidate"], "demo:atlas-2", per_case=1))
    result.traces = _demo_traces(factory, ws, scale, seed)
    return result


def _demo_traces(factory: sessionmaker, ws: Workspace, scale: float, seed: int) -> int:
    import datetime as dt
    rng = random.Random(seed)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with factory.begin() as s:
        s.get(Workspace, ws.id).settings = {"sample_rate": 0.25, "retention_days": 60, "online_judge": "anthropic:claude-haiku-5-5",
                                            "online_evaluators": [{"type": "rule", "max_words": 120, "forbidden": ["as an ai"]}, {"type": "grounded_claims"}]}
    n = int(900 * scale)
    batch = []
    for i in range(n):
        age = 40 * rng.random() ** 1.15                     # More traffic in recent days.
        late = age < 5                                      # The last five days: something changed.
        feature = rng.choices(["support-bot", "search-assist", "summarizer"], [6, 3, 1])[0]
        drift = late and feature == "support-bot"
        model = ("claude-haiku-5-5" if rng.random() < .55 else "gpt-4o-mini") if drift else "gpt-4o-mini"
        base = {"support-bot": 900, "search-assist": 650, "summarizer": 1800}[feature] * (1.7 if drift else 1)
        dur = base * math.exp(rng.gauss(0, .3))
        err = rng.random() < (.07 if drift else .02)
        inp, out = int(rng.gauss(320, 70)), int(rng.gauss(160 if not drift else 210, 40))
        spans = [SpanIn(span_id="m", kind="model", name="generate", offset_ms=dur * .3, duration_ms=dur * .65)]
        if feature != "summarizer":
            spans.insert(0, SpanIn(span_id="r", kind="retrieval", name="search-docs", duration_ms=dur * .25, attrs={"hits": rng.randint(2, 8)}))
        for t in range(rng.randint(2, 4) if drift else (1 if rng.random() < .2 else 0)):
            spans.append(SpanIn(span_id=f"t{t}", kind="tool", name="lookup-order", offset_ms=dur * .3, duration_ms=dur * .1))
        batch.append(TraceIn(trace_id=f"demo-{i}", name=feature, started_at=now - dt.timedelta(days=age), duration_ms=dur, status="error" if err else "ok",
                             error_type=rng.choice(["timeout", "rate_limited"]) if err else None, model=model, prompt_version="v7" if drift else "v6",
                             input="x" * max(inp * 4, 10), output="" if err else "y" * max(out * 4, 10), input_tokens=max(inp, 1), output_tokens=max(out, 1),
                             tags={"plan": rng.choice(["free", "pro", "team"])},
                             feedback=(-1 if rng.random() < (.3 if drift else .1) else 1) if rng.random() < .08 else None, spans=spans))
    for i in range(0, n, 400):
        ingest(factory, ws.id, batch[i:i + 400])
    with factory.begin() as s:
        for t in s.scalars(select(ProductionTrace).where(ProductionTrace.workspace_id == ws.id, ProductionTrace.sampled.is_(True))):
            late = (now - t.started_at).days < 5 and t.name == "support-bot"
            r = random.Random(f"{seed}:{t.trace_id}")
            for evaluator, kind, ok in (("rule", "measured", .97 if not late else .88), ("grounded_claims", "estimated", .91 if not late else .78)):
                if evaluator == "grounded_claims" and t.name == "summarizer":
                    continue
                s.add(TraceEvaluation(trace_pk=t.id, workspace_id=ws.id, evaluator=evaluator, rubric_version="1", kind=kind,
                                      passed=r.random() < ok, score=1.0, rationale="demo data", details={}))
            t.eval_state = "done"
    return n
