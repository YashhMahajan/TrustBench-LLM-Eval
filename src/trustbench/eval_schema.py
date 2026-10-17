"""Check that the output is valid JSON and matches a JSON Schema."""

import json
import re

from jsonschema import Draft202012Validator

from .evaluators import Ctx, Outcome, register


def parse_json(text: str):
    """Parse JSON. Accept output inside a markdown code fence."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return json.loads(m.group(1) if m else text)


@register("json_schema")
def json_schema(ctx: Ctx) -> Outcome:
    schema = ctx.spec.get("schema") or ctx.expected.get("schema")
    if schema is None:
        return Outcome("json_schema", "1", False, 0.0, "No schema in the test case.", {"error": True})
    try:
        data = parse_json(ctx.output)
    except json.JSONDecodeError as e:
        return Outcome("json_schema", "1", False, 0.0, f"Output is not valid JSON: {e.msg}.")
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "root"
        return Outcome("json_schema", "1", False, 0.0, f"{len(errors)} schema error(s). First at {where}: {e.message}")
    return Outcome("json_schema", "1", True, 1.0, "Valid JSON. Matches the schema.")
