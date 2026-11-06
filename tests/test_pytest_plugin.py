"""The pytest plugin, tested the way a user meets it: pytest runs in a subprocess and loads the plugin."""

import json

import pytest

from trustbench.edd.pytest_plugin import NO_STORED, remaining_budget

APP = '''
import pathlib
def answer(prompt):
    pathlib.Path("called.txt").open("a").write("x")        # Leaves a mark each time the app really runs.
    return "Paris" if "France" in prompt else "Lyon"
'''
DATA = [{"id": "a", "input": "Capital of France?", "expected": {"answer": "Paris"}},
        {"id": "b", "input": "Capital of Spain?", "expected": {"answer": "Madrid"}}]          # The app gets this one wrong.


@pytest.fixture
def project(pytester, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")      # Load the plugin only through -p, installed or not.
    monkeypatch.delenv("TRUSTBENCH_LIVE", raising=False)
    monkeypatch.delenv("TRUSTBENCH_DATABASE_URL", raising=False)
    pytester.makepyfile(my_app=APP)
    pytester.path.joinpath("d.jsonl").write_text("\n".join(json.dumps(c) for c in DATA) + "\n")
    pytester.syspathinsert()
    return pytester


def write_test(p, body, model="python:my_app:answer@v1"):
    p.makepyfile(test_eval=f'''
import pytest
MODEL = "{model}"
{body}
''')


def run(p, *args):
    return p.runpytest_subprocess("-p", "trustbench.edd.pytest_plugin", "-p", "no:cacheprovider", *args)


def calls(p):
    f = p.path / "called.txt"
    return len(f.read_text()) if f.exists() else 0


PASS_50 = "def test_x(trustbench):\n    trustbench.assert_eval('d.jsonl', MODEL, min_pass=0.5, confidence_bound=False)\n"


def test_replay_is_the_default_a_miss_fails_and_says_how_to_record(project):
    write_test(project, PASS_50)
    r = run(project)
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines([f"*{NO_STORED}*--trustbench-live*"])
    assert calls(project) == 0                                    # Replay never called the app.


def test_live_records_then_replay_passes_without_calling_the_app(project):
    write_test(project, PASS_50)
    run(project, "--trustbench-live").assert_outcomes(passed=1)
    assert calls(project) == 2
    run(project).assert_outcomes(passed=1)                        # Replay: free.
    assert calls(project) == 2                                    # Still 2: nothing was called.


def test_live_is_also_switched_on_by_the_environment_for_ci(project, monkeypatch):
    write_test(project, PASS_50)
    monkeypatch.setenv("TRUSTBENCH_LIVE", "1")
    run(project).assert_outcomes(passed=1)
    assert calls(project) == 2


def test_a_new_version_label_does_not_replay_the_old_answers(project):
    write_test(project, PASS_50)
    run(project, "--trustbench-live").assert_outcomes(passed=1)
    write_test(project, PASS_50, model="python:my_app:answer@v2")
    r = run(project)
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines([f"*{NO_STORED}*"])


def test_the_gate_fails_a_low_pass_rate_and_names_the_failed_case(project):
    write_test(project, "def test_x(trustbench):\n    trustbench.assert_eval('d.jsonl', MODEL, min_pass=0.9, confidence_bound=False)\n")
    run(project, "--trustbench-live")
    r = run(project)
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines(["*Gate status fail*", "*case b failed*"])


def test_a_confidence_bound_makes_two_cases_inconclusive_and_the_flag_allows_it(project):
    body = "def test_x(trustbench):\n    trustbench.assert_eval('d.jsonl', MODEL, min_pass=0.4, min_cases=5)\n"
    write_test(project, body)
    run(project, "--trustbench-live").assert_outcomes(failed=1)
    r = run(project)
    r.stdout.fnmatch_lines(["*Gate status inconclusive*"])
    write_test(project, "def test_x(trustbench):\n    trustbench.assert_eval('d.jsonl', MODEL, min_pass=0.4, min_cases=5, allow_inconclusive=True)\n")
    run(project).assert_outcomes(passed=1)


def test_a_dirty_tree_without_a_label_explains_why_it_cannot_be_replayed(project, monkeypatch):
    write_test(project, PASS_50, model="python:my_app:answer")
    monkeypatch.setattr("trustbench.providers.app._git_state", lambda: ("abc", True), raising=False)
    r = run(project)                                              # The subprocess sees the real tree (not a git repo here).
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines(["*Add an @label*"])


def test_mock_models_run_for_free_each_time(project):
    write_test(project, "def test_x(trustbench):\n    trustbench.assert_eval('smoke', 'mock:smoke', min_pass=0.5, confidence_bound=False)\n")
    run(project).assert_outcomes(passed=1)


def test_the_marker_is_registered_and_selects_tests(project):
    write_test(project, "@pytest.mark.trustbench\ndef test_marked(trustbench):\n    trustbench.assert_eval('smoke', 'mock:smoke', min_pass=0.5, confidence_bound=False)\n\ndef test_plain():\n    pass\n")
    run(project, "-m", "trustbench", "--strict-markers").assert_outcomes(passed=1, deselected=1)


def test_stored_answers_live_in_a_file_under_the_project_folder(project):
    write_test(project, PASS_50)
    run(project, "--trustbench-live")
    assert (project.path / ".trustbench" / "pytest.db").exists()


def test_the_database_can_be_chosen(project):
    write_test(project, PASS_50)
    run(project, "--trustbench-live", "--trustbench-db", f"sqlite:///{project.path}/other.db").assert_outcomes(passed=1)
    assert (project.path / "other.db").exists() and not (project.path / ".trustbench").exists()


@pytest.mark.parametrize("budget,spent,left", [(None, 5.0, None), (1.0, 0.25, 0.75), (1.0, 3.0, 0.0), (0.0, 0.0, 0.0)])
def test_the_session_budget_counts_down_and_never_goes_negative(budget, spent, left):
    assert remaining_budget(budget, spent) == left


def test_a_failed_critical_case_blocks_only_when_asked(project):
    data = [{**DATA[0]}, {**DATA[1], "metadata": {"critical": True}}]
    project.path.joinpath("d.jsonl").write_text("\n".join(json.dumps(c) for c in data) + "\n")
    write_test(project, "def test_x(trustbench):\n    trustbench.assert_eval('d.jsonl', MODEL, min_pass=0.4, confidence_bound=False)\n")
    run(project, "--trustbench-live").assert_outcomes(passed=1)             # Off by default.
    write_test(project, "def test_x(trustbench):\n    trustbench.assert_eval('d.jsonl', MODEL, min_pass=0.4, confidence_bound=False, critical_failures_allowed=0)\n")
    r = run(project)
    r.assert_outcomes(failed=1)
    r.stdout.fnmatch_lines(["*critical_failures: fail*"])
