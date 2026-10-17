"""The self-test must pass on correct code. And it must FAIL on broken code, or it proves nothing."""

import pytest
from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.selftest import REGISTRY, Result, run_selftest


def by_name(results):
    return {r.name: r for r in results}


def test_all_checks_pass_on_the_real_code():
    results = run_selftest(quick=True, areas=["statistics", "evaluators", "redaction", "judge"])
    failed = [(r.name, r.observed, r.detail) for r in results if not r.passed]
    assert not failed and len(results) >= 12


def test_the_same_seed_gives_the_same_numbers():
    a = run_selftest(quick=True, seed=3, areas=["statistics"])
    b = run_selftest(quick=True, seed=3, areas=["statistics"])
    assert [r.observed for r in a] == [r.observed for r in b]


def test_a_crashing_check_is_reported_not_raised(monkeypatch):
    from trustbench import selftest
    selftest._load()
    monkeypatch.setattr(selftest, "REGISTRY", [("statistics", "boom", "never", lambda ctx: 1 / 0)])
    [r] = run_selftest(quick=True)
    assert not r.passed and "ZeroDivisionError" in r.detail


@pytest.mark.parametrize("module,attr,fake,check_name", [
    ("checks_stats", "wilson_interval", lambda k, n, z=1.96: (k / n - 0.01, k / n + 0.01), "Wilson interval covers the true rate"),
    ("checks_stats", "mcnemar_exact", lambda a, b, alt="two-sided": 0.001, "McNemar test: two equal systems are rarely called different"),
    ("checks_stats", "benjamini_hochberg", lambda p, q=0.05: [x < 0.05 for x in p], "Benjamini-Hochberg controls the false discovery rate"),
    ("checks_stats", "ks_two_sample", lambda a, b: (0.5, 0.01), "Drift tests: same data is rarely flagged, a half-sigma shift is found"),
    ("checks_stats", "brier_score", lambda c, k: 0.0, "Calibration metrics match hand calculations"),
    ("checks_eval", "redact", lambda text, custom=None: (text, {}), "Personal data and secrets are removed"),
    ("checks_eval", "evaluate", None, "Deterministic evaluators agree with a gold set"),
])
def test_each_check_notices_a_broken_implementation(monkeypatch, module, attr, fake, check_name):
    import importlib
    mod = importlib.import_module(f"trustbench.selftest.{module}")
    if attr == "evaluate":
        from trustbench.evaluators import Outcome

        async def always_pass(evaluation, inp, expected, output, judge=None):
            return [Outcome("x", "1", True, 1.0, "")]
        fake = always_pass
    monkeypatch.setattr(mod, attr, fake)
    results = by_name(run_selftest(quick=True))
    assert not results[check_name].passed, f"The check '{check_name}' did not notice the broken code."


def test_a_biased_judge_tool_is_noticed(monkeypatch):
    from trustbench.selftest import checks_eval
    from trustbench.judge_bias import BiasReport

    async def blind(judge, cases, rubric, max_cases=20):
        return BiasReport(cases=8, position_inconsistency=0.0, verbosity_mean_delta=0.0, wording_mean_abs_change=0.0)
    monkeypatch.setattr(checks_eval, "run_bias_probes", blind)    # A bias test that finds nothing, even in a biased judge.
    assert not by_name(run_selftest(quick=True))["Bias tests find a biased judge and clear a fair one"].passed


def test_the_command_exit_codes_and_json(monkeypatch):
    runner = CliRunner(env={"COLUMNS": "200"})
    ok = runner.invoke(app, ["selftest", "--quick", "--area", "statistics"])
    assert ok.exit_code == 0 and "checks passed" in ok.output
    import json
    data = json.loads(runner.invoke(app, ["selftest", "--quick", "--area", "redaction", "--json"]).output)
    assert {d["area"] for d in data} == {"redaction"} and all(d["passed"] for d in data)
    from trustbench.selftest import checks_eval
    monkeypatch.setattr(checks_eval, "redact", lambda text, custom=None: (text, {}))
    bad = runner.invoke(app, ["selftest", "--quick", "--area", "redaction"])
    assert bad.exit_code == 1 and "FAIL" in bad.output
