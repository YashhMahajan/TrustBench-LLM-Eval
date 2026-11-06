"""The workflow and the Action are code that runs with your repository's permissions. They are checked like code."""

import re
import shlex
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from trustbench.cli import app

ROOT = Path(__file__).resolve().parent.parent
FILES = {"workflow": ROOT / ".github" / "workflows" / "ci.yml", "action": ROOT / "action.yml"}
SHA = re.compile(r"^[0-9a-f]{40}$")


def load(name):
    return yaml.safe_load(FILES[name].read_text())


def steps(name):
    d = load(name)
    if name == "action":
        return d["runs"]["steps"]
    return [s for job in d["jobs"].values() for s in job["steps"]]


@pytest.mark.parametrize("name", FILES)
def test_the_files_are_valid_yaml(name):
    assert isinstance(load(name), dict)


@pytest.mark.parametrize("name", FILES)
def test_every_third_party_action_is_pinned_to_a_full_commit_sha(name):
    """A tag can be moved to different code. A commit SHA cannot."""
    for step in steps(name):
        if "uses" in step:
            ref = step["uses"].split("@", 1)
            assert len(ref) == 2 and SHA.match(ref[1]), f"{step['uses']} is not pinned to a SHA"
            assert "#" in FILES[name].read_text().split(step["uses"])[1].split("\n")[0], f"{step['uses']} has no tag comment"


@pytest.mark.parametrize("name", FILES)
def test_no_user_controlled_value_is_written_into_a_shell_script(name):
    """`${{ ... }}` inside `run:` is pasted into the script before it runs. Anything an attacker controls becomes a command.
    Values must go through `env:` and be read as shell variables."""
    for step in steps(name):
        script = step.get("run", "")
        for expr in re.findall(r"\$\{\{(.*?)\}\}", script):
            assert not re.search(r"github\.(event|head_ref|base_ref)|inputs\.|steps\.|env\.", expr), f"unsafe expression in run: {expr.strip()}"


def test_the_workflow_runs_with_the_least_permission_and_ends_when_it_hangs():
    wf = load("workflow")
    assert wf["permissions"] == {"contents": "read"}
    assert all("permissions" not in job or job["permissions"].get("contents") != "write" for job in wf["jobs"].values())
    assert all(job.get("timeout-minutes") for job in wf["jobs"].values())
    assert wf["concurrency"]["cancel-in-progress"] is True


def test_the_workflow_never_runs_pull_request_code_with_write_access():
    text = FILES["workflow"].read_text() + FILES["action"].read_text()
    assert "pull_request_target" not in re.sub(r"#.*", "", FILES["workflow"].read_text())          # Only the warning in the Action's comment.
    assert "workflow_run" not in text


def test_the_workflow_runs_the_checks_we_say_we_run():
    cmds = " ".join(s.get("run", "") for s in steps("workflow"))
    for needle in ["ruff check src tests", "pytest", "trustbench selftest", "docker build", "docker compose config",
                   "pnpm install --frozen-lockfile", "pnpm typecheck", "pnpm build", "-c requirements.lock"]:
        assert needle in cmds, needle


def test_the_action_documents_every_input_and_uses_them_only_through_the_environment():
    inputs = load("action")["inputs"]
    assert all(v.get("description") for v in inputs.values())
    used = set(re.findall(r"inputs\.([a-z-]+)", FILES["action"].read_text()))
    assert used <= set(inputs), used - set(inputs)
    env = next(s["env"] for s in steps("action") if s.get("id") == "check")
    for name in ("config", "target", "baseline-target", "policy", "min-effect", "post-comment"):
        assert f"inputs.{name}" in " ".join(str(v) for v in env.values()), name


def test_the_token_reaches_only_the_step_that_posts_and_is_not_printed():
    text = FILES["action"].read_text()
    assert text.count("github.token") == 1
    holder = next(s for s in steps("action") if "GITHUB_TOKEN" in s.get("env", {}))
    assert holder["id"] == "check" and "echo" not in holder["run"]


def test_the_answers_are_saved_even_when_the_gate_fails_and_the_report_is_always_uploaded():
    by_name = {s.get("name"): s for s in steps("action")}
    assert "always()" in by_name["Save stored answers"]["if"] and by_name["Upload the report"]["if"] == "always()"
    assert "result.json" in by_name["Upload the report"]["with"]["path"] and ".db" not in by_name["Upload the report"]["with"]["path"]


@pytest.mark.parametrize("name", FILES)
def test_every_trustbench_command_in_the_files_exists_and_accepts_its_options(name):
    """A renamed command or option must fail here, not on the first pull request."""
    runner = CliRunner(env={"COLUMNS": "200"})
    for step in steps(name):
        script = step.get("run", "")
        for line in re.findall(r"^\s*(trustbench [^\n$]*)$", script, re.M):
            words = shlex.split(line)[1:]
            command = [w for w in words if not w.startswith("-")][:2]
            help_out = runner.invoke(app, [*command[:2 if command[:1] == ["ci"] else 1], "--help"])
            assert help_out.exit_code == 0, line
            for opt in (w for w in words if w.startswith("--")):
                assert opt in help_out.output, f"{opt} is not an option of '{' '.join(command)}'"


def test_the_options_the_action_passes_exist():
    out = CliRunner(env={"COLUMNS": "300"}).invoke(app, ["ci", "check", "--help"]).output
    script = next(s["run"] for s in steps("action") if s.get("id") == "check")
    for opt in re.findall(r"--[a-z-]+", script):
        assert opt in out, opt
