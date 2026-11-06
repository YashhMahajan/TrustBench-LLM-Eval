"""Run a dataset against models. Two modes: generate and re-score.

How a run works, and why:
  1. One bulk insert creates all executions (not one INSERT for each).
  2. Calls to the model run at the same time, up to `concurrency`, and at most `rpm` start each minute.
  3. Results go to the database in batches (write-behind). One transaction saves many results.
     The run saves a result whole or not at all. So a stopped run never holds half an answer.
  4. A run that stops (Ctrl-C, a crash, a budget) keeps every saved result. `resume_run` does the rest.
"""

import asyncio
import hashlib
import json
import random
import time
import uuid
from dataclasses import asdict, dataclass, fields
from typing import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from . import __version__
from .bulk import insert_many
from .datasets import render_prompt
from .db import (
    EvaluationResult, EvaluationRun, ModelConfiguration, ModelResponse, ResponseCache,
    TestCase, TestExecution, utcnow,
)
from .evaluators import NEEDS_JUDGE, evaluate
from .judge import Judge
from .providers import GenRequest, Provider, ProviderError, estimate_cost, get_provider, split_spec

FLUSH_EVERY = 50          # Results kept in memory before one transaction saves them.
FLUSH_SECONDS = 2.0       # A result waits at most this long. A crash loses at most this much work.
MAX_RETRY_WAIT = 60.0     # We never wait longer than this because of a Retry-After header.
IN_CHUNK = 500            # Size of an IN (...) list. SQLite allows about 32,000 values.


@dataclass
class RunOptions:
    repetitions: int = 1
    concurrency: int = 4
    retries: int = 2
    timeout: float = 60.0
    backoff: float = 0.5
    budget_usd: float | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    system_prompt: str | None = None
    name: str | None = None
    judge: str | None = None  # provider:model of the judge, if a case needs one
    rpm: float | None = None  # Most calls to start in one minute, for each provider. None: no limit.
    cache: bool = True        # Use a stored answer when you made the same request before.


@dataclass
class Progress:
    done: int
    total: int
    failed: int
    cached: int
    cost_usd: float


class RunInterrupted(Exception):
    """The user stopped the run. The run keeps saved results. Use resume_run to finish it."""

    def __init__(self, run_id: str):
        super().__init__(f"Run {run_id} was stopped. Finish it with: trustbench resume {run_id}")
        self.run_id = run_id


def _config_hash(provider: str, model: str, params: dict, system: str | None) -> str:
    raw = json.dumps([provider, model, params, system], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def cache_key(provider: str, model: str, prompt: str, system: str | None, temperature: float,
              max_tokens: int, repetition: int, salt: str = "") -> str:
    """Everything that can change an answer is in the key. The repetition is in it too,
    so repeated runs of one case stay separate calls: we need them to measure variance."""
    parts = [provider, model, prompt, system, temperature, max_tokens, repetition]
    if salt:                       # Only app targets have a salt, so the keys of model answers stay as they were.
        parts.append(salt)
    raw = json.dumps(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_judge(spec: str | None, provider_factory) -> Judge | None:
    if not spec:
        return None
    pname, model = split_spec(spec)
    return Judge(provider_factory(pname), model)


def _needs_judge(cases) -> bool:
    return any(e["type"] in NEEDS_JUDGE for c in cases for e in c.evaluation.get("evaluators", []))


def _chunks(items, size=IN_CHUNK):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _store(session, scored: list, batch: int) -> None:
    """Save (response_id, outcome) pairs with one bulk insert."""
    insert_many(session, EvaluationResult, [
        EvaluationResult(response_id=response_id, batch=batch, evaluator=o.evaluator,
                         rubric_version=o.rubric_version, passed=o.passed, score=o.score,
                         rationale=o.rationale, details=o.details) for response_id, o in scored])


class _Limiter:
    """Spaces the start of calls so that at most `rpm` start in one minute."""

    def __init__(self, rpm: float | None):
        self.gap = 60.0 / rpm if rpm else 0.0
        self.next = 0.0

    async def wait(self) -> None:
        if not self.gap:
            return
        now = time.monotonic()
        start = max(now, self.next)
        self.next = start + self.gap
        if start > now:
            await asyncio.sleep(start - now)


@dataclass
class _Finished:
    """Everything the database needs to know about one execution. Saved in one batch."""
    ex_id: int
    attempts: list[dict]                  # The columns of each ModelResponse, in order of attempt.
    outcomes: list | None                 # Scores for the last attempt. None when no attempt worked.
    status: str                           # done | failed | skipped
    cache_row: dict | None = None


class _Writer:
    """Collects finished executions and saves them in batches."""

    def __init__(self, factory: sessionmaker, every: int = FLUSH_EVERY, seconds: float = FLUSH_SECONDS):
        self.factory, self.every, self.seconds = factory, every, seconds
        self.buffer: list[_Finished] = []
        self.last = time.monotonic()
        self.flushes = 0

    def add(self, item: _Finished) -> None:
        self.buffer.append(item)
        if len(self.buffer) >= self.every or time.monotonic() - self.last >= self.seconds:
            self.flush()

    def flush(self) -> None:
        self.last = time.monotonic()
        if not self.buffer:
            return
        batch, self.buffer = self.buffer, []
        self.flushes += 1
        with self.factory.begin() as s:
            insert_many(s, ModelResponse, [ModelResponse(execution_id=f.ex_id, **a) for f in batch for a in f.attempts])
            ids: dict[tuple[int, int], int] = {}
            for part in _chunks(f.ex_id for f in batch):
                for rid, eid, attempt in s.execute(select(ModelResponse.id, ModelResponse.execution_id, ModelResponse.attempt)
                                                   .where(ModelResponse.execution_id.in_(part))):
                    ids[(eid, attempt)] = rid
            insert_many(s, EvaluationResult, [
                EvaluationResult(response_id=ids[(f.ex_id, f.attempts[-1]["attempt"])], batch=0, evaluator=o.evaluator,
                                 rubric_version=o.rubric_version, passed=o.passed, score=o.score,
                                 rationale=o.rationale, details=o.details)
                for f in batch if f.outcomes for o in f.outcomes])
            for status in {f.status for f in batch}:
                for part in _chunks(f.ex_id for f in batch if f.status == status):
                    s.execute(update(TestExecution).where(TestExecution.id.in_(part)).values(status=status))
        self._save_cache([f.cache_row for f in batch if f.cache_row])

    def _save_cache(self, rows: list[dict]) -> None:
        """A cache problem must never lose a result, so this has its own transaction."""
        if not rows:
            return
        rows = list({r["key"]: r for r in rows}.values())
        try:
            with self.factory.begin() as s:
                have: set[str] = set()
                for part in _chunks(r["key"] for r in rows):
                    have.update(s.scalars(select(ResponseCache.key).where(ResponseCache.key.in_(part))))
                insert_many(s, ResponseCache, [ResponseCache(**r) for r in rows if r["key"] not in have])
        except Exception:                  # Another process saved the same key at the same time. The answer is there.
            pass


def run_eval(
    factory: sessionmaker,
    dataset_version_id: int,
    specs: list[str],
    options: RunOptions | None = None,
    provider_factory: Callable[[str], Provider] = get_provider,
    workspace_id: str | None = None,
    progress: Callable[[Progress], None] | None = None,
) -> str:
    """Generate new outputs and score them. Returns the run id.

    Raises RunInterrupted when the user presses Ctrl-C. The saved part of the run stays in the database."""
    run_id = uuid.uuid4().hex[:12]
    try:
        asyncio.run(_start(factory, run_id, dataset_version_id, list(dict.fromkeys(specs)),
                           options or RunOptions(), provider_factory, workspace_id, progress))
    except KeyboardInterrupt:
        raise RunInterrupted(run_id) from None
    return run_id


def resume_run(
    factory: sessionmaker,
    run_id: str,
    provider_factory: Callable[[str], Provider] = get_provider,
    progress: Callable[[Progress], None] | None = None,
    budget_usd: float | None = None,
) -> str:
    """Finish a run that stopped. It runs the executions that have no result yet, with the options of the run.

    Pass budget_usd to set a new budget for the rest of the run."""
    try:
        asyncio.run(_resume(factory, run_id, provider_factory, progress, budget_usd))
    except KeyboardInterrupt:
        raise RunInterrupted(run_id) from None
    return run_id


Job = tuple[int, TestCase, str, int]            # execution id, case, provider:model, repetition


async def _start(factory, run_id, dataset_version_id, specs, opt: RunOptions, provider_factory, workspace_id, progress) -> None:
    model_ids: dict[str, int] = {}
    params = {"temperature": opt.temperature, "max_tokens": opt.max_tokens}
    for spec in specs:
        split_spec(spec)                                    # Fail early on a bad spec, before we write anything.

    with factory.begin() as s:
        for spec in specs:
            pname, model = split_spec(spec)
            h = _config_hash(pname, model, params, opt.system_prompt)
            mc = s.scalar(select(ModelConfiguration).where(ModelConfiguration.config_hash == h))
            if not mc:
                mc = ModelConfiguration(config_hash=h, provider=pname, model=model,
                                        params=params, system_prompt=opt.system_prompt)
                s.add(mc)
                s.flush()
            model_ids[spec] = mc.id
        cases = s.scalars(select(TestCase).where(TestCase.dataset_version_id == dataset_version_id)
                          .order_by(TestCase.id)).all()
        if _needs_judge(cases) and not opt.judge:
            raise ValueError("This dataset needs a judge model. Add --judge provider:model.")
        s.add(EvaluationRun(
            id=run_id, workspace_id=workspace_id, name=opt.name, dataset_version_id=dataset_version_id,
            config_snapshot={"models": specs, "options": asdict(opt)}, software_version=__version__))
        s.flush()
        insert_many(s, TestExecution, [
            TestExecution(run_id=run_id, test_case_id=case.id, model_config_id=model_ids[spec], repetition=rep)
            for spec in specs for case in cases for rep in range(opt.repetitions)])
        key_of = {(tc, mc, rep): ex for ex, tc, mc, rep in s.execute(
            select(TestExecution.id, TestExecution.test_case_id, TestExecution.model_config_id, TestExecution.repetition)
            .where(TestExecution.run_id == run_id))}
    jobs: list[Job] = [(key_of[(case.id, model_ids[spec], rep)], case, spec, rep)
                       for spec in specs for case in cases for rep in range(opt.repetitions)]
    await _execute(factory, run_id, jobs, specs, opt, provider_factory, progress)


async def _resume(factory, run_id, provider_factory, progress, budget_usd) -> None:
    with factory() as s:
        run = s.get(EvaluationRun, run_id)
        if run is None:
            raise ValueError(f"No run with id {run_id}.")
        known = {f.name for f in fields(RunOptions)}
        opt = RunOptions(**{k: v for k, v in run.config_snapshot["options"].items() if k in known})
        specs = run.config_snapshot["models"]
        rows = s.execute(
            select(TestExecution.id, TestCase, ModelConfiguration, TestExecution.repetition)
            .join(TestCase, TestCase.id == TestExecution.test_case_id)
            .join(ModelConfiguration, ModelConfiguration.id == TestExecution.model_config_id)
            .where(TestExecution.run_id == run_id, TestExecution.status.in_(["pending", "skipped"]))
            .order_by(TestExecution.id)).all()
        prior_cost = run.total_cost_usd - run.judge_cost_usd
    if not rows:
        raise ValueError(f"Run {run_id} has nothing left to do.")
    if budget_usd is not None:
        opt.budget_usd = budget_usd
    elif opt.budget_usd is not None:
        opt.budget_usd = max(opt.budget_usd - prior_cost, 0.0)    # What remains of the old budget.
    jobs: list[Job] = [(ex, case, f"{mc.provider}:{mc.model}", rep) for ex, case, mc, rep in rows]
    with factory.begin() as s:
        for part in _chunks(j[0] for j in jobs):
            s.execute(update(TestExecution).where(TestExecution.id.in_(part)).values(status="pending"))
        s.get(EvaluationRun, run_id).status = "running"
    await _execute(factory, run_id, jobs, specs, opt, provider_factory, progress)


async def _execute(factory, run_id: str, jobs: list[Job], specs: list[str], opt: RunOptions,
                   provider_factory, progress) -> None:
    providers: dict[str, Provider] = {}
    limiters: dict[str, _Limiter] = {}
    judge = None
    writer = _Writer(factory)
    counts = {"done": 0, "failed": 0, "cached": 0}
    spent = 0.0
    try:
        for spec in dict.fromkeys(specs):
            pname, _ = split_spec(spec)
            if pname not in providers:
                providers[pname] = provider_factory(pname)
                limiters[pname] = _Limiter(opt.rpm)
        judge = make_judge(opt.judge, provider_factory)
        sem = asyncio.Semaphore(opt.concurrency)

        cached: dict[str, ResponseCache] = {}
        keys: dict[int, str] = {}
        if opt.cache:
            for ex_id, case, spec, rep in jobs:
                pname, model = split_spec(spec)
                salt = providers[pname].cache_salt(model) if providers[pname].cacheable else None
                if salt is None:
                    continue
                keys[ex_id] = cache_key(pname, model, render_prompt(case.input), opt.system_prompt,
                                        opt.temperature, opt.max_tokens, rep, salt)
            with factory() as s:
                for part in _chunks(set(keys.values())):
                    cached.update({c.key: c for c in s.scalars(select(ResponseCache).where(ResponseCache.key.in_(part)))})

        def tick() -> None:
            if progress:
                progress(Progress(counts["done"] + counts["failed"], len(jobs), counts["failed"], counts["cached"], spent))

        async def one(ex_id: int, case: TestCase, spec: str, rep: int) -> None:
            nonlocal spent
            pname, model = split_spec(spec)
            provider, limiter = providers[pname], limiters[pname]
            req = GenRequest(model=model, prompt=render_prompt(case.input), system=opt.system_prompt,
                             temperature=opt.temperature, max_tokens=opt.max_tokens, timeout=opt.timeout, input=case.input)
            async with sem:
                hit = cached.get(keys.get(ex_id, ""))
                if hit is None and opt.budget_usd is not None and spent >= opt.budget_usd:
                    writer.add(_Finished(ex_id, [], None, "skipped"))
                    tick()
                    return
                attempts: list[dict] = []
                for attempt in range(1, opt.retries + 2):
                    t0 = time.perf_counter()
                    retry_after = None
                    if hit is not None:
                        text, model_id, tin, tout, latency, cost = (
                            hit.text, hit.provider_model_id or model, hit.input_tokens, hit.output_tokens, hit.latency_ms, 0.0)
                        calls, spans = hit.tool_calls, hit.spans
                        r, err, retryable = True, None, False
                        counts["cached"] += 1
                    else:
                        await limiter.wait()
                        try:
                            g = await asyncio.wait_for(provider.generate(req), opt.timeout)
                            r, err, retryable = True, None, False
                            text, model_id, tin, tout, latency = g.text, g.model_id, g.input_tokens, g.output_tokens, g.latency_ms
                            calls, spans = g.tool_calls, g.spans
                            cost = estimate_cost(model_id, tin, tout)
                            spent += cost or 0.0
                        except asyncio.TimeoutError:
                            r, err, retryable = None, "Timeout", True
                        except ProviderError as e:
                            r, err, retryable, retry_after = None, str(e), e.retryable, e.retry_after
                        except Exception as e:  # The run records unknown errors. They do not stop the run.
                            r, err, retryable = None, repr(e), False
                    if r is not None:
                        attempts.append(dict(attempt=attempt, ok=True, output=text, provider_model_id=model_id,
                                             input_tokens=tin, output_tokens=tout, latency_ms=latency, cost_usd=cost,
                                             tool_calls=calls, spans=spans))
                        outcomes = await evaluate(case.evaluation, case.input, case.expected, text, judge,
                                                  tool_calls=calls, spans=spans)
                        row = None if hit is not None or ex_id not in keys else dict(
                            key=keys[ex_id], provider=pname, model=model, text=text, provider_model_id=model_id,
                            input_tokens=tin, output_tokens=tout, latency_ms=latency, tool_calls=calls, spans=spans)
                        counts["done"] += 1
                        writer.add(_Finished(ex_id, attempts, outcomes, "done", row))
                        tick()
                        return
                    attempts.append(dict(attempt=attempt, ok=False, error=err,
                                         latency_ms=(time.perf_counter() - t0) * 1000))
                    if not retryable or attempt > opt.retries:
                        counts["failed"] += 1
                        writer.add(_Finished(ex_id, attempts, None, "failed"))
                        tick()
                        return
                    wait = opt.backoff * 2 ** (attempt - 1) * random.uniform(0.5, 1.0)     # Jitter: calls do not retry together.
                    if retry_after:
                        wait = max(wait, min(retry_after, MAX_RETRY_WAIT))
                    await asyncio.sleep(wait)

        await asyncio.gather(*(one(*j) for j in jobs))
    finally:
        # This also runs when the user presses Ctrl-C: save what is done, mark the run, close the connections.
        writer.flush()
        _finish_run(factory, run_id, spent, judge)
        for p in [*providers.values(), *([judge.provider] if judge else [])]:
            try:
                await p.aclose()
            except Exception:
                pass
    from .overview import refresh_overview
    refresh_overview(factory, run_id)


def _finish_run(factory, run_id: str, spent: float, judge: Judge | None) -> None:
    with factory.begin() as s:
        run = s.get(EvaluationRun, run_id)
        open_work = s.scalar(select(func.count()).select_from(TestExecution).where(
            TestExecution.run_id == run_id, TestExecution.status.in_(["skipped", "pending"])))
        run.status = "incomplete" if open_work else "complete"
        judge_cost = judge.cost_usd if judge else 0.0
        run.total_cost_usd += spent + judge_cost
        run.judge_cost_usd += judge_cost
        run.finished_at = utcnow()


def rescore(factory: sessionmaker, run_id: str, judge_spec: str | None = None,
            provider_factory: Callable[[str], Provider] = get_provider) -> int:
    """Apply the current evaluators to stored outputs. No model calls. Returns the new batch."""
    return asyncio.run(_rescore(factory, run_id, make_judge(judge_spec, provider_factory)))


async def _rescore(factory: sessionmaker, run_id: str, judge: Judge | None) -> int:
    with factory() as s:
        batch = (s.scalar(
            select(func.max(EvaluationResult.batch))
            .join(ModelResponse, ModelResponse.id == EvaluationResult.response_id)
            .join(TestExecution, TestExecution.id == ModelResponse.execution_id)
            .where(TestExecution.run_id == run_id)) or 0) + 1
        rows = s.execute(
            select(ModelResponse, TestCase)
            .join(TestExecution, TestExecution.id == ModelResponse.execution_id)
            .join(TestCase, TestCase.id == TestExecution.test_case_id)
            .where(TestExecution.run_id == run_id, ModelResponse.ok.is_(True))).all()
        if _needs_judge([c for _, c in rows]) and judge is None:
            raise ValueError("This dataset needs a judge model. Add --judge provider:model.")
    try:
        scored = [(resp.id, await evaluate(case.evaluation, case.input, case.expected, resp.output, judge))
                  for resp, case in rows]
        with factory.begin() as s:
            _store(s, [(response_id, o) for response_id, outcomes in scored for o in outcomes], batch)
            if judge:
                run = s.get(EvaluationRun, run_id)
                run.judge_cost_usd += judge.cost_usd
                run.total_cost_usd += judge.cost_usd
    finally:
        if judge:
            await judge.provider.aclose()
    from .overview import refresh_overview
    refresh_overview(factory, run_id)
    return batch
