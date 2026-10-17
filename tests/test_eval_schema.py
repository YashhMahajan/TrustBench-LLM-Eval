import asyncio

from trustbench.evaluators import evaluate

SCHEMA = {"type": "object", "required": ["name", "age"],
          "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
EV = {"evaluators": [{"type": "json_schema", "schema": SCHEMA}]}


def run(output):
    return asyncio.run(evaluate(EV, {}, {}, output))[0]


def test_valid_json_passes_even_inside_a_code_fence():
    assert run('{"name": "Ana", "age": 31}').passed
    assert run('```json\n{"name": "Ana", "age": 31}\n```').passed


def test_wrong_type_names_the_field():
    r = run('{"name": "Ana", "age": "31"}')
    assert not r.passed and "age" in r.rationale


def test_missing_field_fails():
    assert not run('{"name": "Ana"}').passed


def test_not_json_fails_with_clear_reason():
    r = run("Sure! Here is the JSON: {name: Ana}")
    assert not r.passed and "not valid JSON" in r.rationale


def test_missing_schema_is_an_evaluator_problem():
    r = asyncio.run(evaluate({"evaluators": [{"type": "json_schema"}]}, {}, {}, "{}"))[0]
    assert r.details == {"error": True}
