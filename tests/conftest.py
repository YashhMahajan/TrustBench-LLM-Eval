import pytest

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
