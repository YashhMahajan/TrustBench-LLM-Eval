"""Evaluator plugins from other packages: loaded once, never fatal, never able to replace a built-in."""

import importlib.metadata
import sys
import types

import pytest
from typer.testing import CliRunner

from trustbench.cli import app
from trustbench.edd import plugins
from trustbench.evaluators import EVALUATORS, NEEDS_JUDGE, Outcome, register


class FakeEntryPoint:
    def __init__(self, name, load):
        self.name, self._load = name, load

    def load(self):
        return self._load()


@pytest.fixture
def isolated(monkeypatch):
    """Fresh plugin state and a restored evaluator table after each test."""
    saved, judged = dict(EVALUATORS), set(NEEDS_JUDGE)
    monkeypatch.setattr(plugins, "LOADED", [])
    monkeypatch.setattr(plugins, "PLUGIN_ERRORS", {})
    yield
    EVALUATORS.clear()
    EVALUATORS.update(saved)
    NEEDS_JUDGE.clear()
    NEEDS_JUDGE.update(judged)


def offer(monkeypatch, *eps):
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: list(eps) if group == plugins.GROUP else [])


def good_plugin():
    @register("my_check")
    def my_check(ctx):
        return Outcome("my_check", "1", True, 1.0, "ok")

    @register("my_judged", needs_judge=True)
    def my_judged(ctx):
        return Outcome("my_judged", "1", True, 1.0, "ok")


def test_a_plugin_adds_evaluators_and_the_judge_flag_is_kept(isolated, monkeypatch):
    offer(monkeypatch, FakeEntryPoint("good", good_plugin))
    assert plugins.load_evaluator_plugins() == ["good"]
    assert "my_check" in EVALUATORS and "my_judged" in NEEDS_JUDGE and not plugins.PLUGIN_ERRORS


def test_plugins_are_loaded_once(isolated, monkeypatch):
    calls = []
    offer(monkeypatch, FakeEntryPoint("once", lambda: calls.append(1)))
    plugins.load_evaluator_plugins()
    plugins.load_evaluator_plugins()
    assert calls == [1]


def test_a_broken_plugin_is_recorded_and_the_others_still_load(isolated, monkeypatch):
    def broken():
        @register("half_registered")
        def half(ctx):
            return None
        raise RuntimeError("boom")
    offer(monkeypatch, FakeEntryPoint("broken", broken), FakeEntryPoint("good", good_plugin))
    assert plugins.load_evaluator_plugins() == ["good"]
    assert "RuntimeError: boom" in plugins.PLUGIN_ERRORS["broken"]
    assert "half_registered" not in EVALUATORS and "my_check" in EVALUATORS      # What the broken plugin added is removed.


def test_a_plugin_cannot_replace_a_builtin_evaluator(isolated, monkeypatch):
    original = EVALUATORS["exact_match"]

    def evil():
        @register("exact_match")
        def always_true(ctx):
            return Outcome("exact_match", "1", True, 1.0, "hacked")
        register("extra_from_evil")(always_true)
    offer(monkeypatch, FakeEntryPoint("evil", evil))
    assert plugins.load_evaluator_plugins() == []
    assert EVALUATORS["exact_match"] is original and "extra_from_evil" not in EVALUATORS
    assert "replace built-in" in plugins.PLUGIN_ERRORS["evil"]


def test_a_plugin_can_be_a_module_that_registers_on_import(isolated, monkeypatch):
    def import_it():
        mod = types.ModuleType("fake_plugin_module")
        exec("from trustbench.evaluators import register, Outcome\n@register('from_module')\ndef f(ctx): return Outcome('from_module','1',True,1.0,'')", mod.__dict__)
        sys.modules["fake_plugin_module"] = mod
    offer(monkeypatch, FakeEntryPoint("mod", import_it))
    plugins.load_evaluator_plugins()
    assert "from_module" in EVALUATORS
    sys.modules.pop("fake_plugin_module", None)


def test_the_plugins_command_reports_loaded_and_failed_plugins(isolated, monkeypatch):
    runner = CliRunner(env={"COLUMNS": "200"})
    offer(monkeypatch)
    r = runner.invoke(app, ["plugins"])
    assert r.exit_code == 0 and "No evaluator plugins" in r.output
    monkeypatch.setattr(plugins, "LOADED", [])
    monkeypatch.setattr(plugins, "PLUGIN_ERRORS", {})
    offer(monkeypatch, FakeEntryPoint("good", good_plugin), FakeEntryPoint("bad", lambda: 1 / 0))
    r = runner.invoke(app, ["plugins"])
    assert r.exit_code == 1 and "loaded  good" in r.output and "failed  bad" in r.output
