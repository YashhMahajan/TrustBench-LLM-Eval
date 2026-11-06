"""Run evaluations inside pytest.

    def test_support_agent(trustbench):
        trustbench.assert_eval(dataset="datasets/support.jsonl",
                               model="python:myapp.agent:answer@v1", min_pass=0.9)

How it behaves:
  - Default: REPLAY. Answers come from the stored answers (the cache). A plain `pytest` costs nothing.
    If an answer is not stored, the test fails and tells you to record it.
  - `pytest --trustbench-live` (or TRUSTBENCH_LIVE=1, for CI) calls the models and apps for real and stores the answers.
  - `--trustbench-budget USD` stops starting live calls after this spend in one pytest session.
  - The verdict comes from the release gate (gate.py), so it uses confidence intervals, not a bare average.
  - Answers are stored in .trustbench/pytest.db, or in --trustbench-db / TRUSTBENCH_DATABASE_URL.
  - Judge-model calls are not stored. A test that uses a judge needs the judge's API key, even in replay mode.
  - A model with no stored identity cannot be replayed: `mock:` runs for free each time, and a python: target
    with no @label on a dirty git tree is never stored. Add an @label.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

NO_STORED = "No stored answer"


def pytest_addoption(parser):
    group = parser.getgroup("trustbench", "TrustBench evaluations")
    group.addoption("--trustbench-live", action="store_true", default=False,
                    help="Call models and apps for real, and store the answers (default: replay stored answers)")
    group.addoption("--trustbench-budget", type=float, default=None, help="Stop starting live calls after this many USD in the session")
    group.addoption("--trustbench-db", default=None, help="Database URL for stored answers (default: .trustbench/pytest.db)")


def pytest_configure(config):
    config.addinivalue_line("markers", "trustbench: an evaluation test. Select them with -m trustbench.")


def _live(config) -> bool:
    return bool(config.getoption("--trustbench-live")) or os.environ.get("TRUSTBENCH_LIVE", "") not in ("", "0", "false")


def remaining_budget(budget: float | None, spent: float) -> float | None:
    """What a live run may still spend. None means no limit."""
    return None if budget is None else max(budget - spent, 0.0)


def make_provider_factory(live: bool, models: set[str]):
    """Provider factory for one run. In replay mode a stored-answer miss fails the case, with a clear reason."""
    from ..providers import Provider, ProviderError, get_provider

    class ReplayOnly(Provider):
        def __init__(self, inner):
            self.inner, self.name, self.cacheable = inner, inner.name, inner.cacheable

        def cache_salt(self, model):
            return self.inner.cache_salt(model)

        async def generate(self, req):
            if req.model not in models:                        # The judge. Its answers are never stored.
                return await self.inner.generate(req)
            if self.inner.cache_salt(req.model) is None:
                raise ProviderError(f"{NO_STORED}: '{req.model}' has no version label and the git tree is dirty, so it cannot be "
                                    "stored. Add an @label to the target.", retryable=False)
            raise ProviderError(f"{NO_STORED}. Run pytest with --trustbench-live once to record it.", retryable=False)

        async def aclose(self):
            await self.inner.aclose()

    def make(name: str):
        inner = get_provider(name)
        return inner if live or not inner.cacheable else ReplayOnly(inner)
    return make


@dataclass
class EvalResult:
    run_id: str
    status: str                  # The gate status: pass, fail, inconclusive or incomplete.
    scored: int
    pass_rate: float | None
    cost_usd: float
    message: str


class TrustBenchRunner:
    """The `trustbench` fixture."""

    def __init__(self, factory, live: bool, budget: float | None, base: Path, test_name: str):
        self.factory, self.live, self.budget, self.base, self.test_name = factory, live, budget, base, test_name
        self.spent = 0.0

    def _dataset(self, dataset: str):
        from ..datasets import resolve_path
        beside_test = self.base / dataset
        return beside_test if beside_test.exists() else resolve_path(dataset)

    def run(self, dataset: str, model: str, judge: str | None = None, reps: int = 1) -> str:
        """Run a dataset against one model or app and return the run id."""
        from ..datasets import import_dataset, load_cases
        from ..engine import RunOptions, run_eval
        from ..providers import allow_app_targets, split_spec
        from ..tenancy import ensure_default_workspace
        allow_app_targets()
        path = self._dataset(dataset)
        workspace_id = ensure_default_workspace(self.factory).id
        with self.factory() as s:
            version = import_dataset(s, path.stem, load_cases(path), workspace_id)
        opts = RunOptions(repetitions=reps, retries=1, judge=judge, name=f"pytest:{self.test_name}",
                          budget_usd=remaining_budget(self.budget, self.spent) if self.live else None)
        factory = make_provider_factory(self.live, {split_spec(model)[1]})
        run_id = run_eval(self.factory, version.id, [model], opts, provider_factory=factory, workspace_id=workspace_id)
        from ..rundata import load_run
        data = load_run(self.factory, run_id)
        self.spent += sum(r.cost_usd or 0.0 for r in data.records) + data.judge_cost_usd
        return run_id

    def assert_eval(self, dataset: str, model: str, min_pass: float, *, judge: str | None = None, reps: int = 1,
                    min_cases: int = 1, confidence_bound: bool = True, allow_inconclusive: bool = False,
                    critical_failures_allowed: int | None = None) -> EvalResult:
        """Run, then ask the release gate. Fails the test unless the verdict is pass.

        confidence_bound=True (default): the LOW end of the 95% interval must reach min_pass. A small dataset
        gives a wide interval, so 10 of 10 may not prove 90%. Add cases, or set it False to use the average.
        allow_inconclusive=True lets "not enough evidence" pass.
        critical_failures_allowed=0 also fails the test when a case marked metadata.critical fails. It is off by default,
        because a dataset with no critical cases would then always be inconclusive.
        """
        from .. import report
        from ..gate import evaluate_gate
        from ..policy import ReleasePolicy, Rule
        from ..rundata import load_run
        run_id = self.run(dataset, model, judge, reps)
        rows = report.case_rows(self.factory, run_id)
        if any(NO_STORED in (r["error"] or "") for r in rows):
            reason = next(r["error"] for r in rows if NO_STORED in (r["error"] or ""))
            pytest.fail(f"{reason}\n(dataset {dataset}, model {model})", pytrace=False)
        policy = ReleasePolicy(name="pytest", min_cases=min_cases, confidence_bound=confidence_bound,
                               on_insufficient_evidence="allow" if allow_inconclusive else "block",
                               critical_failures_allowed=critical_failures_allowed,
                               overall=Rule(minimum_score=min_pass, min_cases=min_cases))
        data = load_run(self.factory, run_id)
        decision = evaluate_gate(policy, data)
        card = report.summarize(rows)[0]
        lines = [f"  {c.rule}: {c.status} (needs {c.required}; saw {c.observed})" for c in decision.checks if c.status != "pass"]
        failed = [r for r in rows if r["passed"] is False][:5]
        lines += [f"  case {r['case_id']} failed: {r['rationale'] or r['error']}" for r in failed]
        message = f"Gate status {decision.status} for {model} on {dataset} (run {run_id}).\n" + "\n".join(lines)
        result = EvalResult(run_id, decision.status, card["scored"], card["pass_rate"], self.spent, message)
        if not (decision.allowed if allow_inconclusive else decision.status == "pass"):
            pytest.fail(message, pytrace=False)
        return result


@pytest.fixture(scope="session")
def trustbench_factory(request):
    from ..db import make_engine
    from ..migrate import open_db
    url = request.config.getoption("--trustbench-db") or os.environ.get("TRUSTBENCH_DATABASE_URL")
    if not url:
        folder = Path(request.config.rootpath) / ".trustbench"
        folder.mkdir(exist_ok=True)
        url = f"sqlite:///{folder / 'pytest.db'}"
    return open_db(make_engine(url))


@pytest.fixture
def trustbench(request, trustbench_factory):
    """Run and check evaluations. See the top of edd/pytest_plugin.py."""
    spent = request.config.stash.setdefault(_SPENT, [0.0])
    runner = TrustBenchRunner(trustbench_factory, _live(request.config), request.config.getoption("--trustbench-budget"),
                              Path(request.fspath).parent, request.node.name)
    runner.spent = spent[0]
    yield runner
    spent[0] = runner.spent


_SPENT = pytest.StashKey[list]()
