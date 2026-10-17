import pytest

from test_engine import ANSWERS, go
from trustbench.operations import operations
from trustbench.providers import MockProvider
from trustbench.rundata import Record, load_run


def test_latency_percentiles_and_rates():
    records = [Record(case_id=str(i), passed=True, latency_ms=float(i + 1)) for i in range(100)]
    records += [Record(case_id="f1", status="failed", passed=None, attempts=3, timed_out=True),
                Record(case_id="s", status="skipped", passed=None)]
    ops = operations(records)
    assert ops.calls == 101 and ops.latency_n == 100
    assert ops.latency_ms[50] == pytest.approx(50.5) and ops.latency_ms[95] == pytest.approx(95.05)
    assert ops.call_failure_rate == pytest.approx(1 / 101) and ops.call_failure_ci[0] < ops.call_failure_rate
    assert ops.retry_rate == pytest.approx(1 / 101) and ops.timeout_rate == pytest.approx(1 / 101)


def test_cost_per_success_counts_only_passed_cases_and_priced_calls():
    records = [Record(case_id="a", passed=True, cost_usd=0.02), Record(case_id="b", passed=False, cost_usd=0.02),
               Record(case_id="c", passed=True, cost_usd=None)]
    ops = operations(records)
    assert ops.cost_usd == pytest.approx(0.04)
    assert ops.cost_per_success_usd == pytest.approx(0.02)   # 0.04 spent, 2 passes.
    assert ops.priced_share == pytest.approx(2 / 3)


def test_no_prices_means_unknown_cost_not_zero():
    ops = operations([Record(case_id="a", passed=True)])
    assert ops.cost_usd is None and ops.cost_per_success_usd is None


def test_no_calls_gives_no_rates():
    ops = operations([Record(status="skipped", passed=None)])
    assert ops.calls == 0 and ops.call_failure_rate is None and ops.latency_ms[95] is None


def test_from_a_real_run(factory, version):
    rd = load_run(factory, go(factory, version, MockProvider(ANSWERS, fail_first=1)))
    ops = operations(rd.for_model())
    assert ops.calls == 3 and ops.retry_rate == 1.0 and ops.call_failure_rate == 0.0
