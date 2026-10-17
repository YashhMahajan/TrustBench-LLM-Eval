from typer.testing import CliRunner

from trustbench.cli import app

runner = CliRunner()


def test_demo_run_then_results_then_rescore(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    out = runner.invoke(app, ["run", "--model", "mock:smoke"])
    assert out.exit_code == 0, out.output
    assert "mock:smoke" in out.output and "80%" in out.output  # 4 of 5 smoke cases pass.
    run_id = out.output.split("Run ")[1].split()[0].strip("│ ")
    failed = runner.invoke(app, ["results", run_id, "--failed"])
    assert "fact_002" in failed.output and "fact_001" not in failed.output
    assert runner.invoke(app, ["rescore", run_id]).exit_code == 0


def test_no_key_gives_clear_message(monkeypatch, tmp_path):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    out = runner.invoke(app, ["run"])
    assert out.exit_code == 1 and "No model found" in out.output


def test_missing_key_for_chosen_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = runner.invoke(app, ["run", "--model", "openai:gpt-4o-mini"])
    assert out.exit_code == 1 and "OPENAI_API_KEY" in out.output
