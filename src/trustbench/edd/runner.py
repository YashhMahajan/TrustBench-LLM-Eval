"""The steps every way of starting a run shares: load and check the dataset, turn settings into run options.

`trustbench run`, `trustbench ci check` and the pytest plugin all start runs. They use these helpers,
so a dataset is checked the same way, and a setting means the same thing, wherever the run starts.
"""

from ..datasets import DatasetError, import_dataset, load_cases, resolve_path
from ..engine import RunOptions
from ..quality import check as check_dataset
from ..tenancy import ensure_default_workspace
from .config import RunSettings


class RunSetupError(ValueError):
    """The run cannot start. The message says why, in words for the user."""


def load_dataset(factory, dataset: str):
    """Load, check and store a dataset. Returns (version, number of warnings, workspace id)."""
    try:
        path = resolve_path(dataset)
        cases = load_cases(path)
    except DatasetError as e:
        raise RunSetupError(str(e)) from e
    issues = check_dataset(cases)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise RunSetupError(f"The dataset has {len(errors)} error(s). Run 'trustbench check {dataset}' to see them.")
    workspace_id = ensure_default_workspace(factory).id
    with factory() as s:
        version = import_dataset(s, path.stem, cases, workspace_id)
    return version, len(issues), workspace_id


def run_options(settings: RunSettings, *, name: str | None = None, judge: str | None = None,
                system_prompt: str | None = None) -> RunOptions:
    return RunOptions(repetitions=settings.reps, concurrency=settings.concurrency, retries=settings.retries,
                      timeout=settings.timeout, budget_usd=settings.budget, name=name, judge=judge, rpm=settings.rpm,
                      cache=settings.cache, system_prompt=system_prompt)


def read_prompt(path) -> str | None:
    """The text of a system prompt file, or None when no file is set."""
    if path is None:
        return None
    if not path.is_file():
        raise RunSetupError(f"System prompt file not found: {path}")
    return path.read_text()
