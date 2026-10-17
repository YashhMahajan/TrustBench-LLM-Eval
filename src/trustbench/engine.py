"""Run a dataset against models. Two modes: generate and re-score."""

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from . import __version__
from .datasets import render_prompt
from .db import (
    DatasetVersion, EvaluationResult, EvaluationRun, ModelConfiguration, ModelResponse,
    TestCase, TestExecution, utcnow,
)
from .evaluators import NEEDS_JUDGE, evaluate
from .judge import Judge
from .providers import GenRequest, Provider, ProviderError, estimate_cost, get_provider, split_spec


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


def _config_hash(provider: str, model: str, params: dict, system: str | None) -> str:
    raw = json.dumps([provider, model, params, system], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_judge(spec: str | None, provider_factory) -> Judge | None:
    if not spec:
        return None
    pname, model = split_spec(spec)
    return Judge(provider_factory(pname), model)


def _needs_judge(cases) -> bool:
    return any(e["type"] in NEEDS_JUDGE for c in cases for e in c.evaluation.get("evaluators", []))


def _store(session, response_id: int, outcomes, batch: int) -> None:
    for o in outcomes:
        session.add(EvaluationResult(
            response_id=response_id, batch=batch, evaluator=o.evaluator,
            rubric_version=o.rubric_version, passed=o.passed, score=o.score,
            rationale=o.rationale, details=o.details))


def run_eval(
    factory: sessionmaker,
    dataset_version_id: int,
    specs: list[str],
    options: RunOptions | None = None,
    provider_factory: Callable[[str], Provider] = get_provider,
    workspace_id: str | None = None,
) -> str:
    """Generate new outputs and score them. Returns the run id."""
    return asyncio.run(_run(factory, dataset_version_id, specs, options or RunOptions(), provider_factory, workspace_id))


async def _run(factory, dataset_version_id, specs, opt: RunOptions, provider_factory, workspace_id=None) -> str:
    providers: dict[str, Provider] = {}
    model_ids: dict[str, int] = {}
    params = {"temperature": opt.temperature, "max_tokens": opt.max_tokens}
    run_id = uuid.uuid4().hex[:12]

    with factory.begin() as s:
        for spec in specs:
            pname, model = split_spec(spec)
            providers.setdefault(pname, provider_factory(pname))
            h = _config_hash(pname, model, params, opt.system_prompt)
            mc = s.scalar(select(ModelConfiguration).where(ModelConfiguration.config_hash == h))
            if not mc:
                mc = ModelConfiguration(config_hash=h, provider=pname, model=model,
                                        params=params, system_prompt=opt.system_prompt)
                s.add(mc)
                s.flush()
            model_ids[spec] = mc.id
        s.add(EvaluationRun(
            id=run_id, workspace_id=workspace_id, name=opt.name, dataset_version_id=dataset_version_id,
            config_snapshot={"models": specs, "options": asdict(opt)}, software_version=__version__))
        cases = s.scalars(select(TestCase).where(TestCase.dataset_version_id == dataset_version_id)).all()
        if _needs_judge(cases) and not opt.judge:
            raise ValueError("This dataset needs a judge model. Add --judge provider:model.")
        s.flush()
        jobs = []
        for spec in specs:
            for case in cases:
                for rep in range(opt.repetitions):
                    ex = TestExecution(run_id=run_id, test_case_id=case.id,
                                       model_config_id=model_ids[spec], repetition=rep)
                    s.add(ex)
                    s.flush()
                    jobs.append((ex.id, case, spec))

    judge = make_judge(opt.judge, provider_factory)
    sem = asyncio.Semaphore(opt.concurrency)
    spent = 0.0

    async def one(ex_id: int, case: TestCase, spec: str) -> None:
        nonlocal spent
        pname, model = split_spec(spec)
        provider = providers[pname]
        req = GenRequest(model=model, prompt=render_prompt(case.input), system=opt.system_prompt,
                         temperature=opt.temperature, max_tokens=opt.max_tokens, timeout=opt.timeout)
        async with sem:
            if opt.budget_usd is not None and spent >= opt.budget_usd:
                _set_status(factory, ex_id, "skipped")
                return
            for attempt in range(1, opt.retries + 2):
                t0 = time.perf_counter()
                try:
                    r = await asyncio.wait_for(provider.generate(req), opt.timeout)
                except asyncio.TimeoutError:
                    err, retryable, r = "Timeout", True, None
                except ProviderError as e:
                    err, retryable, r = str(e), e.retryable, None
                except Exception as e:  # Unknown errors are recorded. They do not stop the run.
                    err, retryable, r = repr(e), False, None
                if r is not None:
                    cost = estimate_cost(r.model_id, r.input_tokens, r.output_tokens)
                    spent += cost or 0.0
                    outcomes = await evaluate(case.evaluation, case.input, case.expected, r.text, judge)
                    with factory.begin() as s:
                        resp = ModelResponse(
                            execution_id=ex_id, attempt=attempt, ok=True, output=r.text,
                            provider_model_id=r.model_id, input_tokens=r.input_tokens,
                            output_tokens=r.output_tokens, latency_ms=r.latency_ms, cost_usd=cost)
                        s.add(resp)
                        s.flush()
                        _store(s, resp.id, outcomes, batch=0)
                        s.get(TestExecution, ex_id).status = "done"
                    return
                with factory.begin() as s:
                    s.add(ModelResponse(execution_id=ex_id, attempt=attempt, ok=False, error=err,
                                        latency_ms=(time.perf_counter() - t0) * 1000))
                if not retryable or attempt > opt.retries:
                    _set_status(factory, ex_id, "failed")
                    return
                await asyncio.sleep(opt.backoff * 2 ** (attempt - 1))

    await asyncio.gather(*(one(*j) for j in jobs))

    with factory.begin() as s:
        run = s.get(EvaluationRun, run_id)
        skipped = s.scalar(select(func.count()).select_from(TestExecution).where(
            TestExecution.run_id == run_id, TestExecution.status.in_(["skipped", "pending"])))
        run.status = "incomplete" if skipped else "complete"
        run.total_cost_usd = spent + (judge.cost_usd if judge else 0.0)
        run.judge_cost_usd = judge.cost_usd if judge else 0.0
        run.finished_at = utcnow()
    return run_id


def _set_status(factory, ex_id: int, status: str) -> None:
    with factory.begin() as s:
        s.get(TestExecution, ex_id).status = status


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
    scored = [(resp.id, await evaluate(case.evaluation, case.input, case.expected, resp.output, judge))
              for resp, case in rows]
    with factory.begin() as s:
        for response_id, outcomes in scored:
            _store(s, response_id, outcomes, batch)
        if judge:
            run = s.get(EvaluationRun, run_id)
            run.judge_cost_usd += judge.cost_usd
            run.total_cost_usd += judge.cost_usd
    return batch
