import pytest
from typer.testing import CliRunner

from test_engine import ANSWERS, go
from trustbench.cli import app
from trustbench.frontier import Candidate, apply_constraints, candidates_from_runs, pareto
from trustbench.providers import MockProvider
from trustbench.rundata import load_run


def c(name, q, cost, lat, ci=None):
    return Candidate(name, q, ci or (q - 0.02, q + 0.02), 100, cost, lat)


def names(cands):
    return sorted(x.name for x in cands)


def test_dominated_models_are_off_the_frontier():
    big, mid, small, bad = c("big", 0.95, 0.05, 2.0), c("mid", 0.90, 0.02, 1.0), c("small", 0.80, 0.005, 0.5), c("bad", 0.85, 0.06, 3.0)
    front = pareto([big, mid, small, bad])
    assert names(front) == ["big", "mid", "small"]
    assert set(bad.dominated_by) == {"big", "mid"}      # Both are better than bad on quality, cost and latency.


def test_a_model_that_wins_one_measure_stays_on_the_frontier():
    fast = c("fast", 0.70, 0.01, 0.2)
    smart = c("smart", 0.95, 0.10, 4.0)
    assert names(pareto([fast, smart])) == ["fast", "smart"]


def test_equal_models_do_not_beat_each_other():
    a, b = c("a", 0.9, 0.01, 1.0), c("b", 0.9, 0.01, 1.0)
    assert names(pareto([a, b])) == ["a", "b"]


def test_choosing_fewer_measures_changes_the_frontier():
    cheap_slow, dear_fast = c("cheap_slow", 0.9, 0.01, 5.0), c("dear_fast", 0.9, 0.05, 0.5)
    assert names(pareto([cheap_slow, dear_fast])) == ["cheap_slow", "dear_fast"]
    assert names(pareto([cheap_slow, dear_fast], ("quality", "cost"))) == ["cheap_slow"]


def test_unknown_cost_is_not_treated_as_zero():
    free = c("unpriced", 0.99, None, 0.1)
    paid = c("paid", 0.90, 0.02, 1.0)
    front = pareto([free, paid])
    assert names(front) == ["paid"] and free.unknown == ["cost"] and not free.on_frontier
    assert names(pareto([free, paid], ("quality", "latency"))) == ["unpriced"]    # Latency and quality are known for both.


def test_overlapping_quality_intervals_are_flagged():
    strong = c("strong", 0.92, 0.01, 1.0, ci=(0.85, 0.97))
    weak = c("weak", 0.90, 0.02, 2.0, ci=(0.83, 0.95))
    pareto([strong, weak])
    assert weak.dominated_by == ["strong"] and weak.quality_uncertain


def test_constraints_use_the_low_end_of_quality_and_explain_each_failure():
    a = c("a", 0.93, 0.01, 1.0, ci=(0.86, 0.97))
    b = c("b", 0.99, 0.30, 1.0, ci=(0.97, 1.0))
    d = c("d", 0.99, None, None, ci=(0.97, 1.0))
    ok = apply_constraints([a, b, d], min_quality=0.9, max_cost=0.05, max_p95_seconds=2.0)
    assert ok == []
    assert "quality 86.0% (low end) is below 90.0%" in a.why_not_feasible[0]
    assert "cost $0.3000 is above $0.0500" in b.why_not_feasible[0]
    assert set(d.why_not_feasible) == {"cost unknown", "latency unknown"}
    assert names(apply_constraints([a, b], min_quality=0.9, use_lower_bound=False)) == ["a", "b"]


def test_no_limits_means_everything_is_feasible():
    assert len(apply_constraints([c("a", 0.5, 1, 1)])) == 1


def test_candidates_from_real_runs(factory, version, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_PRICES", '{"m1": [1000000, 0], "m2": [2000000, 0]}')
    r1 = load_run(factory, go(factory, version, MockProvider(ANSWERS), specs=("mock:m1", "mock:m2")))
    cands = candidates_from_runs([r1])
    assert [x.name for x in cands] == ["mock:m1", "mock:m2"] and all(x.cases == 3 for x in cands)
    assert cands[1].cost_per_success == pytest.approx(2 * cands[0].cost_per_success)
    both = candidates_from_runs([r1, r1])
    assert both[0].name.startswith(r1.run_id + ":")


def test_frontier_command(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    runner = CliRunner(env={"COLUMNS": "220"})
    out = runner.invoke(app, ["run", "--model", "mock:smoke"])
    rid = out.output.split("Run ")[1].split()[0].strip("│ ")
    res = runner.invoke(app, ["frontier", rid])
    assert res.exit_code == 0 and "mock:smoke" in res.output and "cannot compare: cost unknown" in res.output
    limited = runner.invoke(app, ["frontier", rid, "--min-quality", "0.9", "--max-p95", "5"])
    assert limited.exit_code == 0 and "No model meets all limits" in limited.output
    assert runner.invoke(app, ["frontier", "nope"]).exit_code == 1
