"""The cost model: dollars from pass rates, honest about what is unknown, and wired into the policy and the gate."""

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.edd.costmodel import CostModel, annual_cost, cost_report
from trustbench.gate import evaluate_gate
from trustbench.policy import PolicyError, parse_policy
from trustbench.rundata import Record, RunData

runner = CliRunner(env={"COLUMNS": "200"})

MODEL = dict(volume_per_year=1_000_000, positive_rate=0.05,
             costs=dict(false_positive=2, false_negative=30, true_positive=2),
             manual=dict(fp_rate=0.02, fn_rate=0.03, cost_per_item=0.20))


def model(**kw):
    return CostModel(**{**MODEL, **kw})


def rec(label, passed, cost=0.001, status="done", **kw):
    return Record(case_id=kw.pop("case_id", "c"), status=status, passed=None if status != "done" else passed,
                  metadata={} if label is None else {"positive": label}, cost_usd=cost, **kw)


def records(pos_fail, pos_n, neg_fail, neg_n, cost=0.001):
    return ([rec(True, i >= pos_fail, cost, case_id=f"p{i}") for i in range(pos_n)]
            + [rec(False, i >= neg_fail, cost, case_id=f"n{i}") for i in range(neg_n)])


# ---- The formula ------------------------------------------------------------------------------------

def test_the_formula_gives_the_published_example():
    """Fareed Khan's expense-audit example: manual $380,000 and a perfect system $100,000."""
    m = model()
    assert annual_cost(m, 0.02, 0.03, 0.20) == pytest.approx(380_000)
    assert annual_cost(m, 0.0, 0.0, 0.0) == pytest.approx(100_000)


def test_every_part_of_the_formula_is_used():
    m = model(volume_per_year=1000, positive_rate=0.5, costs=dict(false_positive=1, false_negative=10, true_positive=100, true_negative=1000))
    assert annual_cost(m, 0, 0, 0) == 500 * 100 + 500 * 1000
    assert annual_cost(m, 1, 0, 0) == 500 * 100 + 500 * 1
    assert annual_cost(m, 0, 1, 0) == 500 * 10 + 500 * 1000
    assert annual_cost(m, 0, 0, 0.5) == 500 * 100 + 500 * 1000 + 500


@pytest.mark.parametrize("bad", [dict(positive_rate=0), dict(positive_rate=1), dict(volume_per_year=0),
                                 dict(costs=dict(false_negative=-1)), dict(surprise=1), dict(manual=dict(fp_rate=2, fn_rate=0))])
def test_bad_models_are_rejected(bad):
    with pytest.raises(ValidationError):
        model(**bad)


# ---- The report -------------------------------------------------------------------------------------

def test_error_rates_come_from_failures_on_positive_and_negative_cases():
    rep = cost_report(model(), records(pos_fail=3, pos_n=100, neg_fail=10, neg_n=400))
    assert (rep.fn_rate, rep.fp_rate, rep.positives, rep.negatives) == (0.03, 0.025, 100, 400)
    assert rep.status == "ok" and rep.processing_per_item == pytest.approx(0.001)
    assert rep.annual_cost == pytest.approx(annual_cost(model(), 0.025, 0.03, 0.001))
    assert rep.manual_cost == pytest.approx(380_000) and rep.perfect_cost == pytest.approx(100_000)
    assert rep.savings == pytest.approx(rep.manual_cost - rep.annual_cost)


def test_the_interval_contains_the_estimate_and_is_wider_with_fewer_cases():
    big = cost_report(model(), records(30, 1000, 100, 4000))
    small = cost_report(model(), records(3, 100, 10, 400))
    for r in (big, small):
        assert r.annual_ci[0] <= r.annual_cost <= r.annual_ci[1]
    assert (small.annual_ci[1] - small.annual_ci[0]) > (big.annual_ci[1] - big.annual_ci[0]) * 2
    assert small.savings_ci[0] == pytest.approx(small.manual_cost - small.annual_ci[1])
    assert small.savings_ci[1] == pytest.approx(small.manual_cost - small.annual_ci[0])


def test_a_failed_call_is_a_mistake_and_a_skipped_case_is_left_out():
    recs = [rec(True, None, None, status="failed", case_id="a"), rec(True, True, case_id="b"),
            rec(True, None, status="skipped", case_id="c"), rec(False, True, case_id="d")]
    rep = cost_report(model(), recs)
    assert rep.positives == 2 and rep.fn_rate == 0.5 and rep.negatives == 1


def test_unlabeled_cases_are_left_out_and_reported():
    rep = cost_report(model(), records(1, 10, 1, 10) + [rec(None, True), rec("maybe", False)])
    assert rep.unlabeled == 2 and any("2 executions have no 'positive' label" in n for n in rep.notes)


def test_the_label_key_can_be_changed():
    recs = [Record(case_id=f"c{i}", passed=i % 2 == 0, metadata={"needs_audit": i < 5}, cost_usd=0.0) for i in range(10)]
    rep = cost_report(model(label_key="needs_audit"), recs)
    assert rep.positives == 5 and rep.negatives == 5


def test_an_unknown_cost_of_running_the_system_is_incomplete_and_never_zero():
    rep = cost_report(model(), records(1, 50, 1, 50, cost=None))
    assert rep.status == "incomplete" and rep.annual_cost is None and rep.annual_ci is None and rep.savings is None
    assert rep.without_processing is not None and "processing_cost_per_item" in rep.reason
    one_unpriced = records(1, 50, 1, 50)
    one_unpriced[0].cost_usd = None
    assert cost_report(model(), one_unpriced).status == "incomplete"          # One missing price is enough.


def test_the_cost_of_running_can_be_set_by_hand():
    rep = cost_report(model(processing_cost_per_item=0.05), records(1, 50, 1, 50, cost=None))
    assert rep.status == "ok" and rep.processing_per_item == 0.05


def test_without_both_kinds_of_case_the_report_says_what_to_mark():
    only_positive = cost_report(model(), records(1, 10, 0, 0))
    assert only_positive.status == "incomplete" and "negative" in only_positive.reason and only_positive.annual_cost is None
    assert "positive" in cost_report(model(), records(0, 0, 1, 10)).reason


# ---- Policy and gate --------------------------------------------------------------------------------

POLICY = """
release_policy:
  name: t
  min_cases: 1
  confidence_bound: %s
  critical_failures_allowed: null
  annual_cost_max_usd: %s
  cost_model:
    volume_per_year: 1000000
    positive_rate: 0.05
    costs: {false_positive: 2, false_negative: 30, true_positive: 2}
    manual: {fp_rate: 0.02, fn_rate: 0.03, cost_per_item: 0.20}
"""


def gate(recs, limit, bound="true"):
    pol = parse_policy(POLICY % (bound, limit))
    data = RunData("r", "complete", "d", "h", 0.0, recs)
    return {c.rule: c for c in evaluate_gate(pol, data).checks}["annual_cost"]


def test_a_policy_cost_rule_needs_a_cost_model():
    with pytest.raises(PolicyError, match="needs a cost_model"):
        parse_policy("release_policy:\n  annual_cost_max_usd: 1000\n")
    assert parse_policy("release_policy:\n  name: x\n").cost_model is None


def test_the_gate_fails_a_system_that_costs_too_much():
    c = gate(records(30, 100, 40, 400), limit=100_000)                   # 30% misses: very expensive.
    assert c.status == "fail" and "a year" in c.observed and "95%" in c.observed


def test_the_gate_passes_when_the_whole_interval_is_under_the_limit():
    c = gate(records(1, 1000, 4, 4000), limit=400_000)
    assert c.status == "pass"


def test_a_wide_interval_over_the_limit_is_inconclusive_unless_the_bound_is_off():
    recs = records(3, 100, 10, 400)
    point = cost_report(parse_policy(POLICY % ("true", 1)).cost_model, recs)
    limit = (point.annual_cost + point.annual_ci[1]) / 2                 # Between the estimate and the top of its interval.
    assert gate(recs, limit).status == "inconclusive"
    assert gate(recs, limit, bound="false").status == "pass"


def test_the_gate_cannot_pass_a_cost_it_does_not_know():
    c = gate(records(1, 50, 1, 50, cost=None), limit=10_000_000)
    assert c.status == "inconclusive" and c.observed == "unknown" and "processing_cost_per_item" in c.evidence


def test_the_policy_template_parses_and_its_cost_example_is_valid_when_uncommented():
    import re
    from trustbench.policy import TEMPLATE
    assert parse_policy(TEMPLATE).cost_model is None
    lines = TEMPLATE.split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "# cost_model:")
    end = next(i for i, ln in enumerate(lines) if "annual_cost_max_usd" in ln)
    block = "\n".join(re.sub(r"^  # ?", "  ", ln) for ln in lines[start:end + 1])
    pol = parse_policy("release_policy:\n" + block)
    assert pol.cost_model.volume_per_year == 1_000_000 and pol.annual_cost_max_usd == 150_000
    assert annual_cost(pol.cost_model, 0.02, 0.03, 0.20) == pytest.approx(380_000)


# ---- Command ----------------------------------------------------------------------------------------

@pytest.fixture
def run_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    cases = [{"id": "p1", "input": "What is the capital of France?", "expected": {"answer": "Paris"}, "metadata": {"positive": True}},
             {"id": "p2", "input": "Is this a leap year? 2023", "expected": {"answer": "no"}, "metadata": {"positive": True}},
             {"id": "n1", "input": "What is the refund period?", "expected": {"answer": "The refund period is 30 days."}, "metadata": {"positive": False}}]
    (tmp_path / "d.jsonl").write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    r = runner.invoke(app, ["run", "--dataset", "d.jsonl", "--model", "mock:smoke"])
    assert r.exit_code == 0, r.output
    from trustbench.cli_common import get_factory
    from trustbench.db import EvaluationRun
    with get_factory()() as s:
        return s.query(EvaluationRun).one().id


def write_policy(path, processing=""):
    path.write_text(f"release_policy:\n  name: c\n  cost_model:\n    volume_per_year: 1000\n    positive_rate: 0.5\n{processing}"
                    "    costs: {false_positive: 2, false_negative: 30, true_positive: 2}\n    manual: {fp_rate: 0.1, fn_rate: 0.1, cost_per_item: 1}\n")


def test_the_cost_command_prints_the_report_and_is_incomplete_when_the_price_is_unknown(run_id, tmp_path):
    write_policy(tmp_path / "policy.yaml")
    r = runner.invoke(app, ["cost", run_id])
    assert r.exit_code == 1 and "Incomplete" in r.output and "unknown" in r.output and "Perfect system" in r.output


def test_the_cost_command_with_a_known_processing_cost(run_id, tmp_path):
    write_policy(tmp_path / "policy.yaml", "    processing_cost_per_item: 0.01\n")
    r = runner.invoke(app, ["cost", run_id])
    assert r.exit_code == 0, r.output
    assert "This system" in r.output and "Saving against manual" in r.output and "$" in r.output
    data = json.loads(runner.invoke(app, ["cost", run_id, "--json"]).output)
    assert data["status"] == "ok" and data["positives"] == 2 and data["negatives"] == 1 and data["fn_rate"] == 0.5


def test_the_cost_command_explains_a_policy_without_a_cost_model(run_id, tmp_path):
    (tmp_path / "policy.yaml").write_text("release_policy:\n  name: x\n")
    r = runner.invoke(app, ["cost", run_id])
    assert r.exit_code == 1 and "no cost_model" in r.output
    write_policy(tmp_path / "policy.yaml")
    r = runner.invoke(app, ["cost", "nope"])
    assert r.exit_code == 1 and "not a baseline name or a run id" in r.output
