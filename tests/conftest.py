import pytest

pytest_plugins = ["pytester"]

from trustbench.datasets import CaseSpec, import_dataset
from trustbench.db import init_db, make_engine


@pytest.fixture
def factory():
    return init_db(make_engine("sqlite://"))


@pytest.fixture
def cases():
    return [
        CaseSpec(id="a", input="Capital of France?", expected={"answer": "Paris"}),
        CaseSpec(id="b", input="2+2?", expected={"answer": "4"}),
        CaseSpec(id="c", input="Say hi in under 3 words.",
                 evaluation={"evaluators": [{"type": "rule", "max_words": 3}]}),
    ]


@pytest.fixture
def version(factory, cases):
    with factory() as s:
        return import_dataset(s, "t", cases)


@pytest.fixture(autouse=True)
def _app_targets_off():
    """python: and http: targets are off by default. A test that turns them on must not affect the next test."""
    from trustbench.providers import allow_app_targets
    allow_app_targets(False)
    yield
    allow_app_targets(False)


@pytest.fixture(autouse=True)
def _plain_terminal_output(monkeypatch):
    """GitHub Actions turns on color. Color codes split words in the help text and break checks on the output."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
