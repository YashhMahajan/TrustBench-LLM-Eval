"""Reliability of the calls: errors, retries, latency and cost."""

from dataclasses import dataclass

from .rundata import Record
from .stats import percentile, wilson_interval


@dataclass
class Operations:
    calls: int                       # Executions that ended: done or failed.
    call_failure_rate: float | None
    call_failure_ci: tuple[float, float] | None
    retry_rate: float | None         # Share of calls that needed more than one attempt.
    timeout_rate: float | None
    latency_ms: dict                 # p50, p95, p99 of successful calls.
    latency_n: int
    cost_usd: float | None           # Model cost of the run. None when no price is known.
    cost_per_success_usd: float | None
    priced_share: float | None       # Share of successful calls that had a price.


def operations(records: list[Record]) -> Operations:
    ended = [r for r in records if r.status in ("done", "failed")]
    done = [r for r in ended if r.status == "done"]
    failed = len(ended) - len(done)
    lat = [r.latency_ms for r in done if r.latency_ms is not None]
    priced = [r for r in done if r.cost_usd is not None]
    cost = sum(r.cost_usd for r in priced) if priced else None
    successes = sum(1 for r in done if r.passed)
    return Operations(
        calls=len(ended),
        call_failure_rate=failed / len(ended) if ended else None,
        call_failure_ci=wilson_interval(failed, len(ended)),
        retry_rate=sum(r.attempts > 1 for r in ended) / len(ended) if ended else None,
        timeout_rate=sum(r.timed_out for r in ended) / len(ended) if ended else None,
        latency_ms={p: percentile(lat, p) for p in (50, 95, 99)}, latency_n=len(lat),
        cost_usd=cost,
        cost_per_success_usd=cost / successes if cost is not None and successes else None,
        priced_share=len(priced) / len(done) if done else None,
    )
