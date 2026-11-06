"""Third-party evaluators.

A package can add evaluators without changing TrustBench. In its pyproject.toml:

    [project.entry-points."trustbench.evaluators"]
    my_checks = "my_package.trustbench_evaluators"

TrustBench imports that module when it starts. The module registers its evaluators with
`from trustbench.evaluators import register`. A broken plugin never stops TrustBench. PLUGIN_ERRORS keeps the error, and `trustbench plugins` shows it. A plugin can add evaluators but it cannot replace a built-in one.
"""

import importlib.metadata
import logging

log = logging.getLogger("trustbench")
GROUP = "trustbench.evaluators"
PLUGIN_ERRORS: dict[str, str] = {}      # entry point name -> why TrustBench did not load it
LOADED: list[str] = []


def load_evaluator_plugins() -> list[str]:
    """Import every plugin once. Returns the names that loaded."""
    from ..evaluators import EVALUATORS, NEEDS_JUDGE
    if LOADED or PLUGIN_ERRORS:
        return LOADED
    for ep in importlib.metadata.entry_points(group=GROUP):
        before, judged = dict(EVALUATORS), set(NEEDS_JUDGE)
        try:
            ep.load()
        except Exception as e:       # A plugin is somebody else's code. Whatever it raises, we carry on.
            PLUGIN_ERRORS[ep.name] = f"{type(e).__name__}: {e}"
            log.warning("TrustBench plugin '%s' was not loaded: %s", ep.name, e)
            _restore(before, judged)
            continue
        replaced = [n for n, fn in before.items() if EVALUATORS.get(n) is not fn]
        if replaced:
            _restore(before, judged)
            PLUGIN_ERRORS[ep.name] = f"it tried to replace built-in evaluators {replaced}"
            log.warning("TrustBench plugin '%s' was rejected: %s", ep.name, PLUGIN_ERRORS[ep.name])
            continue
        LOADED.append(ep.name)
    return LOADED


def _restore(before: dict, judged: set) -> None:
    from ..evaluators import EVALUATORS, NEEDS_JUDGE
    EVALUATORS.clear()
    EVALUATORS.update(before)
    NEEDS_JUDGE.clear()
    NEEDS_JUDGE.update(judged)
