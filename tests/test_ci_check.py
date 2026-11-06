"""`trustbench ci check` end to end: real runs of a small Python app, the gate, the comment, the exit codes."""

import json

import pytest
from typer.testing import CliRunner

from trustbench.ci.check import CheckError, run_check
from trustbench.cli import app
from trustbench.edd.config import parse_config

runner = CliRunner(env={"COLUMNS": "200"})
APP = '''
def good(prompt): return "Paris" if "France" in prompt else "Madrid"
def bad(prompt): return "Lyon"
'''
# 60 identical pairs cannot prove "no worse than 5 points" (the interval is about +-6), so the test policy allows 10.
NO_BASELINE_POLICY = "release_policy:\n  name: solo\n  min_cases: 20\n  critical_failures_allowed: null\n  overall: {minimum_score: 0.8}\n"
POLICY = "release_policy:\n  name: ci\n  min_cases: 20\n  critical_failures_allowed: null\n  overall: {minimum_score: 0.8, maximum_regression: 0.10}\n"


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("TRUSTBENCH_DATABASE_URL", f"sqlite:///{tmp_path}/ci.db")
    for var in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_REPOSITORY", "GITHUB_TOKEN", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "ci_app.py").write_text(APP)
    cases = [{"id": f"c{i}", "task": "geo", "input": "Capital of France?" if i % 2 else "Capital of Spain?",
              "expected": {"answer": "Paris" if i % 2 else "Madrid"}} for i in range(60)]
    (tmp_path / "d.jsonl").write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    (tmp_path / "policy.yaml").write_text(POLICY)
    (tmp_path / "solo.yaml").write_text(NO_BASELINE_POLICY)
    (tmp_path / "trustbench.yaml").write_text(
        "dataset: d.jsonl\ntargets:\n  main: 'python:ci_app:good@main'\n  worse: 'python:ci_app:bad@pr'\n  same: 'python:ci_app:good@pr'\n")
    return tmp_path


def check(*args):
    return runner.invoke(app, ["ci", "check", *args])


def test_a_candidate_that_matches_the_baseline_passes_and_the_files_are_written(work):
    r = check("--target", "same", "--baseline-target", "main")
    assert r.exit_code == 0, r.output
    assert "Gate: pass" in r.output
    comment = (work / "trustbench-comment.md").read_text()
    assert comment.startswith("<!-- trustbench-report -->") and "✅ pass" in comment and "Compared with run" in comment
    result = json.loads((work / "trustbench-result.json").read_text())
    assert result["status"] == "pass" and result["allowed"] is True and result["pass_rate"] == 1.0 and result["baseline_run_id"]


def test_a_regression_blocks_with_exit_code_1(work):
    r = check("--target", "worse", "--baseline-target", "main")
    assert r.exit_code == 1, r.output
    result = json.loads((work / "trustbench-result.json").read_text())
    assert result["status"] == "fail" and result["allowed"] is False
    assert {c["rule"]: c["status"] for c in result["checks"]}["overall.maximum_regression"] == "fail"
    assert "❌ fail" in (work / "trustbench-comment.md").read_text()


def test_the_gate_decision_is_saved_with_its_evidence(work):
    check("--target", "worse", "--baseline-target", "main")
    from trustbench.cli_common import get_factory
    from trustbench.db import ReleaseDecision
    with get_factory()() as s:
        d = s.query(ReleaseDecision).one()
        assert d.status == "fail" and d.allowed is False and d.baseline_run_id and d.policy_name == "ci"


def test_without_a_policy_nothing_is_enforced_and_the_comment_says_results(work):
    (work / "policy.yaml").unlink()
    r = check("--target", "worse")
    assert r.exit_code == 0 and "no policy" in r.output
    assert "## TrustBench: results" in (work / "trustbench-comment.md").read_text()


def test_a_regression_rule_without_a_baseline_cannot_be_decided_and_blocks(work):
    r = check("--target", "same")
    assert r.exit_code == 1 and "Gate: inconclusive" in r.output


def test_without_a_baseline_only_the_policy_rules_apply(work):
    assert check("--target", "same", "--policy", "solo.yaml").exit_code == 0
    assert check("--target", "worse", "--policy", "solo.yaml").exit_code == 1          # 0% is under the 80% floor.


def test_exit_code_2_means_the_check_could_not_run(work):
    for args, needle in [(["--target", "nope"], "Unknown target"), ([], "Say which target"),
                         (["--config", "missing.yaml", "--target", "same"], "Config file not found")]:
        r = check(*args)
        assert r.exit_code == 2 and needle in r.output, (args, r.output)
    (work / "trustbench.yaml").write_text("targets: {a: 'mock:smoke'}\n")
    assert "no dataset" in check("--target", "a").output
    (work / "trustbench.yaml").write_text("dataset: d.jsonl\ntargets: {a: 'mock:smoke'}\nbaseline: ghost\n")
    r = check("--target", "a")
    assert r.exit_code == 2 and "does not exist here" in r.output


def test_a_baseline_name_from_the_config_is_used_when_it_exists(work):
    check("--target", "main")
    from trustbench.cli_common import get_factory
    from trustbench.db import EvaluationRun
    with get_factory()() as s:
        run_id = s.query(EvaluationRun).one().id
    assert runner.invoke(app, ["baseline", "set", "main", run_id]).exit_code == 0
    (work / "trustbench.yaml").write_text(
        "dataset: d.jsonl\nbaseline: main\ntargets: {worse: 'python:ci_app:bad@pr'}\n")
    r = check("--target", "worse")
    assert r.exit_code == 1 and "overall.maximum_regression" in (work / "trustbench-comment.md").read_text()


def test_a_bad_policy_file_is_exit_code_2(work):
    (work / "policy.yaml").write_text("release_policy:\n  surprise: 1\n")
    assert check("--target", "same").exit_code == 2


# ---- Posting ----------------------------------------------------------------------------------------

@pytest.fixture
def posted(monkeypatch):
    calls = []

    def fake(token, repo, pr, body, **kw):
        calls.append((token, repo, pr, body))
        return "created", 42
    monkeypatch.setattr("trustbench.ci.github.post_or_update", fake)
    return calls


def pr_event(work, monkeypatch, number=7):
    event = work / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": number}}))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")


def test_in_a_pull_request_workflow_the_comment_is_posted_with_the_number_from_the_event(work, monkeypatch, posted):
    pr_event(work, monkeypatch)
    assert check("--target", "same", "--policy", "solo.yaml").exit_code == 0
    assert len(posted) == 1 and posted[0][:3] == ("tok", "acme/app", 7) and posted[0][3].startswith("<!-- trustbench-report -->")


def test_a_failing_gate_still_posts_the_comment_and_still_blocks(work, monkeypatch, posted):
    pr_event(work, monkeypatch)
    assert check("--target", "worse").exit_code == 1
    assert len(posted) == 1 and "❌ fail" in posted[0][3]


def test_outside_a_pull_request_nothing_is_posted_unless_asked(work, monkeypatch, posted):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    check("--target", "same", "--policy", "solo.yaml")
    assert posted == []
    r = check("--target", "same", "--policy", "solo.yaml", "--post", "--repo", "acme/app", "--pr", "3")
    assert posted[0][:3] == ("tok", "acme/app", 3) and r.exit_code == 0


def test_no_post_wins_over_the_event(work, monkeypatch, posted):
    pr_event(work, monkeypatch)
    check("--target", "same", "--policy", "solo.yaml", "--no-post")
    assert posted == []


def test_a_problem_with_posting_is_a_warning_and_never_changes_the_verdict(work, monkeypatch):
    from trustbench.ci.github import GithubError

    def boom(*a, **k):
        raise GithubError("GitHub answered 403 when we tried to post the comment.")
    monkeypatch.setattr("trustbench.ci.github.post_or_update", boom)
    pr_event(work, monkeypatch)
    ok, bad = check("--target", "same", "--policy", "solo.yaml"), check("--target", "worse")
    assert ok.exit_code == 0 and bad.exit_code == 1 and "Could not post the comment" in ok.output


def test_a_missing_token_or_number_is_a_warning(work, monkeypatch, posted):
    pr_event(work, monkeypatch)
    monkeypatch.delenv("GITHUB_TOKEN")
    r = check("--target", "same", "--policy", "solo.yaml")
    assert r.exit_code == 0 and "Not posting the comment" in r.output and posted == []
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    (work / "event.json").write_text("not json")
    assert "Not posting the comment" in check("--target", "same", "--policy", "solo.yaml").output


def test_the_step_summary_is_written_even_when_posting_is_impossible(work, monkeypatch):
    summary = work / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    check("--target", "same", "--policy", "solo.yaml")
    assert "TrustBench" in summary.read_text()


# ---- The other commands -----------------------------------------------------------------------------

def test_comment_builds_a_comment_for_an_existing_run_and_post_sends_a_file(work, monkeypatch, posted):
    check("--target", "main")
    from trustbench.cli_common import get_factory
    from trustbench.db import EvaluationRun
    with get_factory()() as s:
        run_id = s.query(EvaluationRun).one().id
    r = runner.invoke(app, ["ci", "comment", run_id, "--policy", "policy.yaml"])
    assert r.exit_code == 0 and r.output.startswith("<!-- trustbench-report -->") and "✅ pass" in r.output
    assert runner.invoke(app, ["ci", "comment", run_id, "--out", "c.md"]).exit_code == 0 and (work / "c.md").exists()
    assert runner.invoke(app, ["ci", "comment", "nope"]).exit_code == 1
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert runner.invoke(app, ["ci", "post", "c.md", "--repo", "acme/app", "--pr", "9"]).exit_code == 0
    assert posted[0][:3] == ("tok", "acme/app", 9)
    assert runner.invoke(app, ["ci", "post", "missing.md"]).exit_code == 1


def test_run_check_wants_exactly_one_candidate():
    cfg = parse_config("dataset: smoke\ntargets: {a: 'mock:a', b: 'mock:b'}\n")
    with pytest.raises(CheckError, match="Say which target"):
        run_check(None, cfg)
