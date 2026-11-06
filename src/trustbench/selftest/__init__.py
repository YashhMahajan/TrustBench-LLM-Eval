"""The TrustBench self-test: does TrustBench measure correctly?

A reliability tool must show that its own measurements are valid. Each check here builds data where
we KNOW the truth, runs the real TrustBench code on it, and compares the result with what theory or
construction says it must be. For example: a 95% interval must contain the truth about 95% of the time.

Targets are fixed from theory before the numbers are seen. A check that fails is a finding, not a flaw of the check.
Run it with: trustbench selftest        (add --quick for a faster run with fewer simulations)
"""

import random
import time
import traceback
from dataclasses import dataclass
from typing import Callable


@dataclass
class Result:
    area: str
    name: str
    passed: bool
    observed: str
    target: str
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Ctx:
    seed: int = 0
    quick: bool = False

    def n(self, full: int) -> int:
        """How many repetitions a simulation uses. Quick mode uses a quarter."""
        return max(10, full // 4) if self.quick else full

    def rng(self, label: str) -> random.Random:
        return random.Random(f"{self.seed}:{label}")


Check = Callable[[Ctx], tuple[bool, str, str]]     # Returns (passed, observed, detail).
REGISTRY: list[tuple[str, str, str, Check]] = []


def check(area: str, name: str, target: str):
    def wrap(fn: Check) -> Check:
        REGISTRY.append((area, name, target, fn))
        return fn
    return wrap


AREAS = ["statistics", "evaluators", "redaction", "judge", "regression", "robustness", "integrity", "invariants", "privacy"]


def _load() -> None:
    from . import checks_agents, checks_eval, checks_stats  # noqa: F401  (registers the checks)
    try:
        from . import checks_system  # noqa: F401
    except ImportError:
        pass


def run_selftest(quick: bool = False, seed: int = 0, areas: list[str] | None = None,
                 on_result: Callable[[Result], None] | None = None) -> list[Result]:
    _load()
    ctx = Ctx(seed, quick)
    results = []
    for area, name, target, fn in REGISTRY:
        if areas and area not in areas:
            continue
        t0 = time.perf_counter()
        try:
            passed, observed, detail = fn(ctx)
        except Exception as e:      # A check that crashes is a failure of the check, shown with its reason.
            passed, observed, detail = False, "the check crashed", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        r = Result(area, name, bool(passed), observed, target, detail, time.perf_counter() - t0)
        results.append(r)
        if on_result:
            on_result(r)
    return results
