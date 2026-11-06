"""Eval-Driven Development: the pieces that turn evaluation into a daily workflow.

  config.py         trustbench.yaml: what to test, with which models or apps, and how to judge it
  plugins.py        third-party evaluators, found through the `trustbench.evaluators` entry point group
  pytest_plugin.py  run evaluations inside pytest, with replay from the cache by default
"""
