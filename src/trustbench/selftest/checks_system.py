"""Do the pieces work together? We break things on purpose and see whether TrustBench notices.

The model in these checks is the demo model, made perfect (the "oracle") or made worse in one known way.
"""

import copy
import datetime as dt
import json
import random

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from .. import baselines, crypto, quality
from ..comparison import compare_runs
from ..dataprotect import update_policy
from ..datasets import CaseSpec, import_dataset
from ..db import EvaluationResult, ModelResponse, ProductionTrace, TestCase, init_db, make_engine
from ..demo import PROFILES, DemoProvider, Profile, demo_cases
from ..engine import RunOptions, rescore, run_eval
from ..failures import collect_failures
from ..gate import evaluate_gate
from ..policy import ReleasePolicy, Rule
from ..providers import MockProvider
from ..rag import diagnose
from ..report import case_rows
from ..robustness import robustness_report
from ..rundata import Record, RunData, load_run
from ..slices import regression_scan
from ..tenancy import create_api_key, create_workspace
from ..traces import SpanIn, TraceIn, get_trace, ingest, purge_expired
from ..transforms import TRANSFORMS, build_variants
from . import Ctx, check

TASKS = ["factual_qa", "grounded_qa", "abstention", "instruction_following", "structured_output", "prompt_injection", "calibration", "safety"]


def memory():
    return init_db(make_engine("sqlite://"))


def profile(skill: float = 1.0, fragility: float = 0.0, **over) -> Profile:
    return Profile({t: over.get(t, skill) for t in TASKS}, 500.0, (0.0, 0.0), fragility)


def run(factory: sessionmaker, cases: list[CaseSpec], provider, model: str, name: str = "t", ws=None) -> RunData:
    with factory() as s:
        v = import_dataset(s, f"ds-{name}", cases, ws)
    rid = run_eval(factory, v.id, [f"demo:{model}"], RunOptions(backoff=0, concurrency=8, name=name),
                   provider_factory=lambda n: provider, workspace_id=ws)
    return load_run(factory, rid)


def base_cases() -> list[CaseSpec]:
    return demo_cases()


# ---- Regression engine ------------------------------------------------------------------------

def _wrong_reference(cases: list[CaseSpec]) -> tuple[list[CaseSpec], str]:
    out = copy.deepcopy(cases)
    for c in out:
        if c.task != "factual_qa" or hash((c.metadata.get("source_case") or c.id)) % 2:
            continue
        if "number" in c.expected:
            c.expected["number"] += 1
        for e in c.evaluation.get("evaluators", []):
            if e["type"] == "contains":
                e["values"] = ["zzz"]
            elif e["type"] == "rule" and "regex" in e:
                e["regex"] = r"\bzzz\b"
    return out, "factual_qa"


def _flip_abstain(cases):
    out = copy.deepcopy(cases)
    for c in out:
        if c.task == "abstention":
            c.expected["abstain"] = not c.expected["abstain"]
            c.evaluation = {"evaluators": [{"type": "abstain"}]}
    return out, "abstention"


def _lose_evidence(cases):
    out = copy.deepcopy(cases)
    for c in out:
        if c.task == "grounded_qa":
            c.input["context"] = "The office is cleaned every evening. A newsletter is sent once a month."
    return out, "grounded_qa"


@check("regression", "Every kind of injected failure is caught, located and blocked", "5 of 5: the task is named as worse, no other task is blamed, the release is never allowed")
def injected_failures(ctx: Ctx):
    cases = base_cases()
    factory = memory()
    oracle = DemoProvider(cases, {"oracle": profile(), "ignores-json": profile(structured_output=0.0), "bad-prompt": profile(grounded_qa=0.4)})
    baseline = run(factory, cases, oracle, "oracle", "baseline")
    policy = ReleasePolicy(min_cases=20, critical_failures_allowed=None, overall=Rule(maximum_regression=0.02))
    injections = [
        ("model stops writing valid JSON", cases, "ignores-json", "structured_output"),
        ("a faulty prompt hurts one task", cases, "bad-prompt", "grounded_qa"),
        ("a correct reference is replaced by a wrong one", *_wrong_reference(cases)[:1], "oracle", "factual_qa"),
        ("answerable cases now require abstention", *_flip_abstain(cases)[:1], "oracle", "abstention"),
        ("the evidence no longer reaches the model", *_lose_evidence(cases)[:1], "oracle", "grounded_qa"),
    ]
    problems, lines, scan_found = [], [], 0
    for i, (label, cand_cases, model, task) in enumerate(injections):
        cand = run(factory, cand_cases, oracle, model, f"cand{i}")
        cmp = compare_runs(baseline, cand, "demo:oracle", f"demo:{model}")
        d = cmp.by_task.get(task)
        others_hurt = [t for t, x in cmp.by_task.items() if t != task and x.verdict in ("meaningful_regression", "regression_below_threshold")]
        scan = [s for s in regression_scan(baseline.for_model("demo:oracle"), cand.for_model(f"demo:{model}")) if s.regression]
        scan_found += any(s.dimension == "task" and s.value == task for s in scan)
        gate = evaluate_gate(policy, cand, f"demo:{model}", baseline, "demo:oracle")
        ok = (d is not None and d.verdict == "meaningful_regression" and cmp.overall.diff < 0 and not others_hurt and not gate.allowed)
        lines.append(f"{label}: {'caught' if ok else 'MISSED'} ({cmp.overall.diff:+.1%} overall, gate {gate.status})")
        if not ok:
            problems.append(f"{label} (task verdict {d.verdict if d else None}, other tasks hurt {others_hurt}, gate {gate.status})")
    note = f"The slice scan, which corrects for many tests, also named the task in {scan_found} of {len(injections)}. " + " | ".join(lines)
    return not problems, f"{len(injections) - len(problems)} of {len(injections)} caught", "; ".join(problems) or note


@check("regression", "Lost evidence is blamed on retrieval, not on the model", "at least 90% of the failures are labelled retrieval_failure")
def blames_retrieval(ctx: Ctx):
    cases = base_cases()
    factory = memory()
    oracle = DemoProvider(cases, {"oracle": profile()})
    broken, _ = _lose_evidence(cases)
    cand = run(factory, broken, oracle, "oracle", "lost")
    fails = [f for f in collect_failures(cand.records) if f.task == "grounded_qa"]
    blamed = sum(diagnose(f.record).stage == "retrieval_failure" for f in fails)
    rate = blamed / len(fails) if fails else 0.0
    return rate >= 0.9 and len(fails) >= 20, f"{rate:.0%} of {len(fails)} failures", "the answer text is no longer in the context, so the diagnosis must point upstream"


@check("regression", "The same output is never blocked; equal systems rarely are", "identical runs (192 cases) pass a 5 point limit. Equal-skill systems fail a 2 point limit in at most 5% of trials")
def false_alarms(ctx: Ctx):
    cases = base_cases()
    factory = memory()
    oracle = DemoProvider(cases, {"oracle": profile(0.9)})
    one = run(factory, cases, oracle, "oracle", "one")
    two = run(factory, cases, oracle, "oracle", "two")
    policy = ReleasePolicy(min_cases=20, critical_failures_allowed=None, overall=Rule(maximum_regression=0.05))
    identical = evaluate_gate(policy, two, "demo:oracle", one, "demo:oracle")
    policy = ReleasePolicy(min_cases=20, critical_failures_allowed=None, overall=Rule(maximum_regression=0.02))

    rng, trials, blocked = ctx.rng("gate"), ctx.n(150), 0
    for _ in range(trials):
        mk = lambda: RunData("r", "complete", "d", "h", 0.0, [Record(case_id=f"c{i}", passed=rng.random() < 0.85) for i in range(200)])
        blocked += evaluate_gate(policy, mk(), baseline=mk()).status == "fail"
    rate = blocked / trials
    return identical.status == "pass" and rate <= 0.05, f"identical runs: {identical.status}; equal systems blocked {rate:.1%} of {trials}", "200 cases, both systems pass 85% of the time"


# ---- Robustness ------------------------------------------------------------------------------

@check("robustness", "Our transforms never change the right answer", "0 unfair variants in every built-in suite, every transform used")
def transforms_are_fair(ctx: Ctx):
    from ..datasets import load_cases, resolve_path
    names = list(TRANSFORMS)
    rejected = 0
    applied = {n: 0 for n in names}
    made = 0
    for suite in ["factual", "grounded", "abstention", "instruction_following", "structured_output", "injection", "safety", "calibration"]:
        variants, rep = build_variants(load_cases(resolve_path(suite)), names)
        rejected += sum(rep.rejected.values())
        made += len(variants)
        for n in names:
            applied[n] += rep.applied[n]
    unused = [n for n, k in applied.items() if k == 0]
    return rejected == 0 and not unused, f"{rejected} unfair of {made} variants", f"unused transforms: {unused}" if unused else "every transform made variants"


@check("robustness", "A model that is truly robust shows no fragility", "break rate 0 and consistency 100%")
def no_inflation(ctx: Ctx):
    cases = base_cases()
    rep = robustness_report(run(memory(), cases, DemoProvider(cases, {"oracle": profile()}), "oracle", "robust").records)
    ok = rep.pairs > 50 and rep.break_rate.k == 0 and rep.consistency.k == rep.consistency.n
    return ok, f"{rep.pairs} pairs, break rate {rep.break_rate.k}/{rep.break_rate.n}, consistent {rep.consistency.k}/{rep.consistency.n}", "A report that blamed this model would mean the test copies are unfair"


@check("robustness", "A fragile model is exposed", "break rate clearly above zero (interval low end above 5%)")
def finds_fragility(ctx: Ctx):
    cases = base_cases()
    rep = robustness_report(run(memory(), cases, DemoProvider(cases, {"fragile": profile(0.95, fragility=0.35)}), "fragile", "fragile").records)
    low = rep.break_rate.ci[0] if rep.break_rate and rep.break_rate.ci else 0
    return low > 0.05, f"break rate {rep.break_rate.k}/{rep.break_rate.n}, interval low end {low:.1%}", ""


# ---- Benchmark integrity ---------------------------------------------------------------------

@check("integrity", "Planted dataset defects are found, and a clean dataset raises no alarm", "at least 95% of planted defects of each kind found, 0 false alarms")
def planted_defects(ctx: Ctx):
    rng = ctx.rng("integrity")
    clean = [c for c in base_cases() if not c.metadata.get("source_case")]
    false_alarms = quality.check(clean)
    long_enough = [c for c in clean if len(str(c.input).split()) >= 14 and ("answer" in c.expected or c.expected.get("required_facts"))]
    picks = rng.sample(long_enough, 13)
    planted: list[tuple[str, str, CaseSpec]] = []
    for c in picks[:5]:
        d = copy.deepcopy(c); d.id = f"{c.id}-dup"
        planted.append(("duplicate", d.id, d))
    for c in picks[5:10]:
        d = copy.deepcopy(c); d.id = f"{c.id}-near"
        key = "prompt" if "prompt" in d.input else "question"
        d.input[key] = d.input[key] + " please"
        planted.append(("near_duplicate", d.id, d))
    for c in picks[10:13]:
        d = copy.deepcopy(c); d.id = f"{c.id}-conflict"
        d.expected = {"answer": "something else entirely", "required_facts": ["something else entirely"]}
        planted.append(("conflicting_answers", d.id, d))
    for i, kind in enumerate(["numeric", "abstain", "json_schema", "grounded_claims"]):
        planted.append(("missing_reference", f"missing-{i}", CaseSpec(id=f"missing-{i}", input=f"A unique question number {i} that nobody else has asked", evaluation={"evaluators": [{"type": kind}]})))
    pii = ["write to {x}@gmail.com now", "call +1 415-555-01{x}", "my social security number is 123-45-67{x}", "pay with card 4111 1111 1111 1111 now {x}"]
    for i in range(8):
        planted.append(("possible_pii", f"pii-{i}", CaseSpec(id=f"pii-{i}", input=pii[i % 4].format(x=10 + i) + f" and reference number {i}", expected={"answer": "ok"})))
    issues = quality.check(clean + [c for _, _, c in planted])
    found: dict[str, list[bool]] = {}
    for kind, cid, _ in planted:
        found.setdefault(kind, []).append(any(i.code == kind and cid in i.case_ids for i in issues))
    recall = {k: sum(v) / len(v) for k, v in found.items()}
    total_found = sum(sum(v) for v in found.values()) / len(planted)
    ok = not false_alarms and all(r >= 0.95 for r in recall.values()) and total_found >= 0.95
    return ok, f"{total_found:.0%} of {len(planted)} defects found, {len(false_alarms)} false alarms", "by kind: " + ", ".join(f"{k} {v:.0%}" for k, v in recall.items())


# ---- Rules of the data ---------------------------------------------------------------------------

@check("invariants", "The seven rules that make results auditable hold", "all rules hold")
def data_rules(ctx: Ctx):
    cases = [CaseSpec(id=f"q{i}", input=f"Question number {i} is?", expected={"answer": "yes"}) for i in range(12)]
    factory = memory()
    failures = []
    with factory() as s:
        v1 = import_dataset(s, "d", cases)
        v1b = import_dataset(s, "d", list(reversed(cases)))
        changed = [cases[0].model_copy(update={"expected": {"answer": "no"}}), *cases[1:]]
        v2 = import_dataset(s, "d", changed)
        old = s.scalar(select(TestCase).where(TestCase.dataset_version_id == v1.id, TestCase.case_id == "q0"))
        if not (v1.id == v1b.id and v1.id != v2.id and old.expected == {"answer": "yes"}):
            failures.append("1: a dataset version changed or was not reused")
    provider = MockProvider({"Question": "yes"}, fail_first=1)
    rid = run_eval(factory, v1.id, ["mock:m"], RunOptions(backoff=0, repetitions=2), provider_factory=lambda n: provider)
    rd = load_run(factory, rid)
    with factory() as s:
        run_row = s.get(__import__("trustbench.db", fromlist=["EvaluationRun"]).EvaluationRun, rid)
        if not (run_row.config_snapshot.get("models") == ["mock:m"] and run_row.config_snapshot["options"]["repetitions"] == 2 and run_row.software_version):
            failures.append("2: the run did not store a snapshot of its configuration")
        results = s.scalars(select(EvaluationResult)).all()
        if not results or any(not r.evaluator or not r.rubric_version for r in results):
            failures.append("3: a score does not name its evaluator and rubric")
        responses = s.scalars(select(ModelResponse)).all()
        # The mock fails the first call for each question. Two repetitions share a question, so 12 calls fail and 24 succeed.
        if not (sum(not r.ok for r in responses) == 12 and sum(r.ok for r in responses) == 24):
            failures.append("5: failed attempts were not kept as rows next to the retries")
        before = sorted((r.id, r.output) for r in responses)
    calls = provider.calls
    rescore(factory, rid)
    with factory() as s:
        after = sorted((r.id, r.output) for r in s.scalars(select(ModelResponse)).all())
        batches = {r.batch for r in s.scalars(select(EvaluationResult)).all()}
    if before != after or batches != {0, 1}:
        failures.append("4: re-scoring changed the raw outputs, or did not add a batch of scores")
    if provider.calls != calls:
        failures.append("4: re-scoring called the model")
    short = MockProvider({"Question": "yes"})
    with factory() as s:
        v3 = import_dataset(s, "d3", cases)
    import os
    os.environ["TRUSTBENCH_PRICES"] = json.dumps({"m": [1e6, 1e6]})
    try:
        rid2 = run_eval(factory, v3.id, ["mock:m"], RunOptions(backoff=0, concurrency=1, budget_usd=0.0001), provider_factory=lambda n: short)
    finally:
        os.environ.pop("TRUSTBENCH_PRICES", None)
    rd2 = load_run(factory, rid2)
    gate = evaluate_gate(ReleasePolicy(min_cases=1, critical_failures_allowed=None), rd2)
    if not (rd2.status == "incomplete" and gate.status == "incomplete" and not gate.allowed):
        failures.append(f"6: an unfinished run was shown as {rd2.status} and the gate said {gate.status}")
    policy = ReleasePolicy(min_cases=5, critical_failures_allowed=None, overall=Rule(minimum_score=0.5))
    d = evaluate_gate(policy, rd)
    baselines.save_decision(factory, rid, "mock:m", None, policy, d)
    saved = baselines.list_decisions(factory, rid)[0]
    if not (saved.policy_snapshot["overall"]["minimum_score"] == 0.5 and saved.checks):
        failures.append("7: a decision did not keep its policy and its evidence")
    return not failures, f"{7 - len({f[0] for f in failures})} of 7 hold", "; ".join(failures)


# ---- Privacy and separation in production ------------------------------------------------------

def _dump(factory) -> str:
    """Every value of every table, as one text."""
    parts = []
    with factory() as s:
        for table in inspect(factory.kw["bind"]).get_table_names():
            for row in s.execute(text(f'SELECT * FROM "{table}"')).fetchall():
                parts.extend(str(v) for v in row if v is not None)
    return "\n".join(parts)


@check("privacy", "Planted personal data never reaches the database", "0 of the planted values found anywhere in any table")
def no_leaks(ctx: Ctx):
    rng = ctx.rng("leaks")
    factory = memory()
    import os
    old = os.environ.get("TRUSTBENCH_SECRET_KEY")
    os.environ["TRUSTBENCH_SECRET_KEY"] = crypto.generate_key()
    try:
        secrets = []
        for mode in ("measurements only", "stored and encrypted"):
            ws = create_workspace(factory, mode)
            update_policy(factory, ws.id, store_content=mode != "measurements only")
            batch = []
            for i in range(ctx.n(80)):
                email = f"user{rng.randrange(10**6)}.{mode[:3]}@gmail.com"
                ssn = f"{rng.randrange(100, 899)}-{rng.randrange(10, 99)}-{rng.randrange(1000, 9999)}"
                phone = f"+1 415-555-{rng.randrange(1000, 9999)}"
                key = f"sk-{''.join(rng.choice('abcdefghijklmnop') for _ in range(30))}"
                secrets += [email, ssn, phone, key]
                batch.append(TraceIn(trace_id=f"{mode[:3]}-{i}", name="bot", input=f"Please help {email}, my ssn is {ssn}", output=f"Call {phone} for {key}",
                                     context=f"Customer {email} lives here", tags={"user": email},
                                     spans=[SpanIn(name="lookup", kind="tool", attrs={"query": f"find {ssn}"})]))
            ingest(factory, ws.id, batch)
        dump = _dump(factory)
        leaked = [v for v in secrets if v in dump]
        from ..traces import reveal
        with factory() as s:
            revealed = " ".join(" ".join(v for v in reveal(r).values() if v) for r in s.scalars(select(ProductionTrace)) if r.input_enc)
        leaked += [v for v in secrets if v in revealed]
    finally:
        if old is None:
            os.environ.pop("TRUSTBENCH_SECRET_KEY", None)
        else:
            os.environ["TRUSTBENCH_SECRET_KEY"] = old
    return not leaked, f"{len(leaked)} of {len(secrets)} planted values found", "checked every table, and the text that can be read back with the key"


@check("privacy", "Workspaces cannot read each other's data", "0 leaks in every probe")
def isolation(ctx: Ctx):
    from fastapi.testclient import TestClient
    from ..api import create_app
    factory = memory()
    dataset = [CaseSpec(id=f"q{i}", input=f"Question {i} unique", expected={"answer": "yes"}) for i in range(6)]
    world = []
    for name in ("alpha", "beta", "gamma"):
        ws = create_workspace(factory, name)
        _, key = create_api_key(factory, ws.id, "k", "admin")
        with factory() as s:
            v = import_dataset(s, f"{name}-data", dataset, ws.id)
        provider = MockProvider({"Question": "yes"})
        rid = run_eval(factory, v.id, ["mock:m"], RunOptions(backoff=0), provider_factory=lambda n: provider, workspace_id=ws.id)
        ingest(factory, ws.id, [TraceIn(trace_id=f"{name}-t{i}", name="bot") for i in range(5)])
        world.append({"name": name, "key": key, "run": rid, "traces": [f"{name}-t{i}" for i in range(5)], "dataset": v.id})
    client = TestClient(create_app(factory))
    leaks, probes = [], 0
    for me in world:
        h = {"Authorization": f"Bearer {me['key']}"}
        own = client.get("/v1/traces?limit=100", headers=h).json()["traces"]
        if {t["trace_id"] for t in own} != set(me["traces"]):
            leaks.append(f"{me['name']} sees the wrong list of traces")
        if {r["id"] for r in client.get("/v1/runs", headers=h).json()["runs"]} != {me["run"]}:
            leaks.append(f"{me['name']} sees the wrong list of runs")
        if {d["id"] for d in client.get("/v1/datasets", headers=h).json()["datasets"]} != {me["dataset"]}:
            leaks.append(f"{me['name']} sees the wrong list of datasets")
        for other in world:
            if other is me:
                continue
            urls = [f"/v1/traces/{other['traces'][0]}", f"/v1/runs/{other['run']}", f"/v1/runs/{other['run']}/cases", f"/v1/runs/{other['run']}/failures",
                    f"/v1/runs/{other['run']}/slices", f"/v1/runs/{other['run']}/robustness", f"/v1/datasets/{other['dataset']}",
                    f"/v1/compare?candidate={other['run']}&baseline={other['run']}", f"/v1/frontier?runs={other['run']}"]
            for u in urls:
                probes += 1
                if client.get(u, headers=h).status_code != 404:
                    leaks.append(f"{me['name']} reached {u}")
            probes += 1
            if client.delete(f"/v1/traces/{other['traces'][0]}", headers=h).status_code != 404:
                leaks.append(f"{me['name']} deleted a trace of {other['name']}")
    still = all(get_trace(factory, _ws_id(factory, w["name"]), w["traces"][0]) for w in world)    # Each workspace still has its own data.
    return not leaks and still, f"{len(leaks)} leaks in {probes + 6} probes", "; ".join(leaks[:3])


def _ws_id(factory, name):
    from ..tenancy import get_workspace
    return get_workspace(factory, name).id


@check("privacy", "Retention deletes exactly the traces that are past due", "no trace kept too long, none deleted too early, spans gone with them")
def retention(ctx: Ctx):
    rng = ctx.rng("retention")
    factory = memory()
    ws = create_workspace(factory, "w")
    update_policy(factory, ws.id, retention_days=10)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    ages = {f"t{i}": rng.uniform(0, 20) for i in range(ctx.n(200))}
    ingest(factory, ws.id, [TraceIn(trace_id=k, started_at=now - dt.timedelta(days=a), spans=[SpanIn(name="s")]) for k, a in ages.items()])
    purge_expired(factory)
    kept = {k for k in ages if get_trace(factory, ws.id, k)}
    due = {k for k, a in ages.items() if a >= 10}
    early = {k for k in ages if k not in due and k not in kept}
    late = kept & due
    with factory() as s:
        from ..db import TraceSpan
        spans = len(s.scalars(select(TraceSpan)).all())
    return not early and not late and spans == len(kept), f"{len(late)} kept too long, {len(early)} deleted too early, {spans} spans for {len(kept)} traces", ""


@check("privacy", "No task is lost when a worker dies", "every task is finished, none twice at the same time")
def tasks_survive(ctx: Ctx):
    from ..jobs import DbQueue, run_worker
    factory = memory()
    q = DbQueue(factory)
    for i in range(30):
        q.enqueue("a", {"n": i}, max_attempts=3)
    for _ in range(10):
        q.claim("crashed-worker", lease_seconds=3600)        # Taken by a worker ...
    from ..db import JobRow, utcnow
    with factory.begin() as s:                               # ... that then died: its leases run out.
        for row in s.scalars(select(JobRow).where(JobRow.state == "running")):
            row.lease_expires_at = utcnow() - dt.timedelta(seconds=1)
    done = []
    run_worker(q, {"a": lambda p, j: done.append(p["n"])}, concurrency=1, idle_exit=True, poll_interval=0.01)
    c = q.counts()
    return c["done"] == 30 and c["dead"] == 0 and set(done) == set(range(30)), f"{c['done']} of 30 finished, {c['dead']} dead", f"{len(done)} runs for 30 tasks: 10 tasks ran again after their worker died"


@check("invariants", "The migrations build exactly the schema of the models", "the database made by the migrations has no difference from the models")
def schema_matches(ctx: Ctx):
    import tempfile, os
    from .. import migrate
    with tempfile.TemporaryDirectory() as d:
        engine = make_engine(f"sqlite:///{os.path.join(d, 's.db')}")
        what = migrate.ensure_schema(engine)
        diffs = migrate.describe(migrate.differences(engine))
        engine.dispose()
    return what == "created" and not diffs, f"{what}, {len(diffs)} differences", "; ".join(diffs[:4]) or "a change to a model needs a new migration"
