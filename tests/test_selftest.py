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


# ---- The whole-system checks: they pass on the real code and fail on code that is broken on purpose ----------------

SYSTEM = ["regression", "robustness", "integrity", "invariants", "privacy"]


def test_the_system_checks_pass_on_the_real_code():
    results = run_selftest(quick=True, areas=SYSTEM)
    assert len(results) >= 12
    assert not [(r.name, r.observed, r.detail) for r in results if not r.passed]


def broken(monkeypatch, target, replacement):
    mod, _, attr = target.rpartition(".")
    import importlib
    monkeypatch.setattr(importlib.import_module(mod), attr, replacement)


def failed_names(areas):
    return {r.name for r in run_selftest(quick=True, areas=areas) if not r.passed}


def test_a_gate_that_lets_everything_through_is_noticed(monkeypatch):
    from trustbench.gate import Decision
    broken(monkeypatch, "trustbench.selftest.checks_system.evaluate_gate", lambda *a, **k: Decision("pass", [], True))
    assert "Every kind of injected failure is caught, located and blocked" in failed_names(["regression"])


def test_a_gate_that_blocks_everything_is_noticed(monkeypatch):
    from trustbench.gate import Decision
    broken(monkeypatch, "trustbench.selftest.checks_system.evaluate_gate", lambda *a, **k: Decision("fail", [], False))
    assert "The same output is never blocked; equal systems rarely are" in failed_names(["regression"])


def test_a_comparison_that_sees_no_change_is_noticed(monkeypatch):
    from trustbench import comparison
    real = comparison.compare_runs

    def blind(base, cand, bm=None, cm=None, min_effect=0.01):
        return real(base, base, bm, bm, min_effect)       # Always compares the baseline with itself.
    broken(monkeypatch, "trustbench.selftest.checks_system.compare_runs", blind)
    assert "Every kind of injected failure is caught, located and blocked" in failed_names(["regression"])


def test_a_diagnosis_that_never_blames_retrieval_is_noticed(monkeypatch):
    from trustbench.rag import RagDiagnosis
    broken(monkeypatch, "trustbench.selftest.checks_system.diagnose", lambda rec: RagDiagnosis("evidence_ignored", True, 0.5))
    assert "Lost evidence is blamed on retrieval, not on the model" in failed_names(["regression"])


def test_an_unfair_transform_is_noticed(monkeypatch):
    from trustbench import transforms
    from trustbench.transforms import Transform

    def adds_a_number(case, rng):
        key = "prompt" if "prompt" in case.input else "question" if "question" in case.input else None
        return case.model_copy(update={"input": {**case.input, key: case.input[key] + " 999"}}) if key else None
    patched = {**transforms.TRANSFORMS, "typos": Transform("typos", "broken", adds_a_number)}
    monkeypatch.setattr(transforms, "TRANSFORMS", patched)
    broken(monkeypatch, "trustbench.selftest.checks_system.TRANSFORMS", patched)
    assert "Our transforms never change the right answer" in failed_names(["robustness"])


def test_a_dataset_check_that_finds_nothing_is_noticed(monkeypatch):
    broken(monkeypatch, "trustbench.quality.check", lambda cases: [])
    assert "Planted dataset defects are found, and a clean dataset raises no alarm" in failed_names(["integrity"])


def test_a_dataset_check_that_cries_wolf_is_noticed(monkeypatch):
    from trustbench.quality import Issue
    broken(monkeypatch, "trustbench.quality.check", lambda cases: [Issue("warning", "duplicate", [c.id for c in cases[:2]], "x")])
    assert "Planted dataset defects are found, and a clean dataset raises no alarm" in failed_names(["integrity"])


def test_broken_data_rules_are_noticed(monkeypatch):
    from trustbench import engine
    real = engine.rescore

    def rescore_with_a_call(factory, run_id, judge_spec=None, provider_factory=None):
        raise RuntimeError("re-scoring touched the model")
    broken(monkeypatch, "trustbench.selftest.checks_system.rescore", rescore_with_a_call)
    assert "The seven rules that make results auditable hold" in failed_names(["invariants"])


def test_a_leak_of_personal_data_is_noticed(monkeypatch):
    from trustbench import traces
    from trustbench.dataprotect import Protected
    import hashlib
    broken(monkeypatch, "trustbench.traces.redact", lambda text, custom=None: (text, {}))
    broken(monkeypatch, "trustbench.traces.protect",
           lambda policy, text: Protected(None, None, text, hashlib.sha256(text.encode()).hexdigest(), len(text), False, {}))
    assert "Planted personal data never reaches the database" in failed_names(["privacy"])


def test_a_workspace_that_can_read_another_is_noticed(monkeypatch):
    broken(monkeypatch, "trustbench.api_data.owned", lambda column, ws: True)
    assert "Workspaces cannot read each other's data" in failed_names(["privacy"])


def test_retention_that_deletes_nothing_is_noticed(monkeypatch):
    broken(monkeypatch, "trustbench.selftest.checks_system.purge_expired", lambda factory, now=None: 0)
    assert "Retention deletes exactly the traces that are past due" in failed_names(["privacy"])


def test_a_queue_that_forgets_dead_workers_is_noticed(monkeypatch):
    from sqlalchemy import select
    from trustbench.db import JobRow, utcnow
    from trustbench.jobs import DbQueue, Job

    def claim_new_only(self, worker_id, kinds=None, lease_seconds=120):
        with self.factory.begin() as s:
            row = s.scalars(select(JobRow).where(JobRow.state == "queued").order_by(JobRow.id)).first()
            if row is None:
                return None
            row.state, row.attempts, row.worker_id = "running", row.attempts + 1, worker_id
            return Job(str(row.id), row.kind, row.payload, row.workspace_id, row.attempts, row.max_attempts)
    monkeypatch.setattr(DbQueue, "claim", claim_new_only)
    assert "No task is lost when a worker dies" in failed_names(["privacy"])


def test_a_model_change_without_a_migration_is_noticed():
    from sqlalchemy import Column, Integer
    from trustbench.db import Base
    Base.metadata.tables["jobs"].append_column(Column("forgotten", Integer, nullable=True))
    try:
        assert "The migrations build exactly the schema of the models" in failed_names(["invariants"])
    finally:
        Base.metadata.tables["jobs"]._columns.remove(Base.metadata.tables["jobs"].c.forgotten)


# ---- The remaining checks: each must also fail when the code it watches is broken ---------------------------------

def test_a_paired_interval_that_is_too_narrow_or_too_sure_is_noticed(monkeypatch):
    broken(monkeypatch, "trustbench.selftest.checks_stats.paired_binary_ci",
           lambda a, b: ((sum(b) - sum(a)) / len(a) - 0.005, (sum(b) - sum(a)) / len(a) + 0.005))     # The bootstrap's false certainty.
    names = failed_names(["statistics"])
    assert "Paired interval covers the true change, also at the extremes" in names
    assert "Paired interval does not claim certainty from a few cases" in names


def test_a_test_with_no_power_is_noticed(monkeypatch):
    broken(monkeypatch, "trustbench.selftest.checks_stats.mcnemar_exact", lambda a, b, alt="two-sided": 1.0)
    assert "McNemar test: a real 12 point drop is found" in failed_names(["statistics"])


def test_a_wrong_chi_square_tail_is_noticed(monkeypatch):
    broken(monkeypatch, "trustbench.selftest.checks_stats.chi2_sf", lambda x, df: 0.5)
    assert "Chi-square tail matches the published table" in failed_names(["statistics"])


def test_redaction_that_damages_ordinary_text_is_noticed(monkeypatch):
    import re
    broken(monkeypatch, "trustbench.selftest.checks_eval.redact", lambda text, custom=None: (re.sub(r"\d+", "[NUMBER]", text), {}))
    assert "Ordinary text is left alone" in failed_names(["redaction"])


def test_a_biased_agreement_measure_is_noticed(monkeypatch):
    broken(monkeypatch, "trustbench.selftest.checks_eval.weighted_kappa", lambda a, b, levels, kind="quadratic": 0.95)
    assert "Agreement numbers about a judge are unbiased and their interval is honest" in failed_names(["judge"])


def test_unfair_test_copies_that_look_fragile_are_noticed(monkeypatch):
    from trustbench.robustness import RobustnessReport, Rate
    blamed = RobustnessReport(pairs=122, break_rate=Rate(30, 100, (0.2, 0.4)), consistency=Rate(70, 122, (0.5, 0.6)))
    broken(monkeypatch, "trustbench.selftest.checks_system.robustness_report", lambda records: blamed)
    names = failed_names(["robustness"])
    assert "A model that is truly robust shows no fragility" in names


def test_a_robustness_report_that_finds_nothing_is_noticed(monkeypatch):
    from trustbench.robustness import RobustnessReport, Rate
    blind = RobustnessReport(pairs=122, break_rate=Rate(0, 100, (0.0, 0.04)), consistency=Rate(122, 122, (0.97, 1.0)))
    broken(monkeypatch, "trustbench.selftest.checks_system.robustness_report", lambda records: blind)
    assert "A fragile model is exposed" in failed_names(["robustness"])


def test_every_check_has_a_test_that_breaks_the_code_it_watches():
    """A check that was never seen to fail proves nothing. Add a sabotage test when you add a check."""
    from pathlib import Path
    from trustbench import selftest
    selftest._load()
    source = Path(__file__).read_text()
    missing = [name for _, name, _, _ in selftest.REGISTRY if f'"{name}"' not in source]
    assert not missing, f"No sabotage test for: {missing}"


def test_a_cache_that_never_hits_is_noticed(monkeypatch):
    import uuid
    broken(monkeypatch, "trustbench.engine.cache_key", lambda *a, **k: uuid.uuid4().hex)
    assert "A repeated or resumed run never pays for the same model call twice" in failed_names(["invariants"])


def test_a_resume_that_starts_over_is_noticed(monkeypatch):
    from trustbench.engine import RunOptions, run_eval

    def starts_over(factory, run_id, provider_factory=None, progress=None, budget_usd=None):
        from trustbench.db import EvaluationRun, TestCase, TestExecution
        from sqlalchemy import select, update
        with factory.begin() as s:                              # Wrongly puts every case back to work, finished or not.
            s.execute(update(TestExecution).where(TestExecution.run_id == run_id).values(status="skipped"))
        from trustbench import engine
        return engine.resume_run(factory, run_id, provider_factory, progress, budget_usd)
    broken(monkeypatch, "trustbench.selftest.checks_system.resume_run", starts_over)
    assert "A repeated or resumed run never pays for the same model call twice" in failed_names(["invariants"])
