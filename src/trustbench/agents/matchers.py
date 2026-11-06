"""How a test case says what a value should be.

A plain value must be equal:            {"order_id": "A1"}      (1 equals 1.0, but True does not equal 1)
A matcher is a dict with one of these:  {"approx": 10.0, "tol": 0.01}   absolute tolerance (or "rel": 0.01)
                                        {"regex": "^A\\d+$"}            must match the start of a string
                                        {"in": ["a", "b"]}              one of these values
                                        {"contains": "refund"}          substring, or item of a list
                                        {"type": "number"}              string, number, integer, boolean, array, object
                                        {"schema": {...}}               a JSON Schema
                                        {"any": true}                   the value only has to exist
"""

import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

TYPES = {"string": str, "boolean": bool, "array": list, "object": dict}
KEYS = {"approx", "regex", "in", "contains", "type", "schema", "any"}
EXTRA = {"approx": {"tol", "rel"}}


def is_matcher(m) -> bool:
    return isinstance(m, dict) and bool(m) and bool(KEYS & set(m))


def validate_matcher(m) -> None:
    """Raise ValueError if a matcher is malformed. A dataset with a bad matcher must not load."""
    if not is_matcher(m):
        return
    main = KEYS & set(m)
    if len(main) != 1:
        raise ValueError(f"A matcher has exactly one of {sorted(KEYS)}, found {sorted(main)}.")
    kind = next(iter(main))
    extra = set(m) - main - EXTRA.get(kind, set())
    if extra:
        raise ValueError(f"Matcher '{kind}' does not take {sorted(extra)}.")
    v = m[kind]
    if kind == "approx" and (isinstance(v, bool) or not isinstance(v, (int, float))):
        raise ValueError("'approx' needs a number.")
    if kind == "approx" and any(not isinstance(m.get(k, 0), (int, float)) or m.get(k, 0) < 0 for k in ("tol", "rel")):
        raise ValueError("'tol' and 'rel' must be numbers that are not negative.")
    if kind == "regex":
        try:
            re.compile(v)
        except (re.error, TypeError) as e:
            raise ValueError(f"'regex' is not a valid pattern: {e}") from e
    if kind == "in" and not isinstance(v, list):
        raise ValueError("'in' needs a list.")
    if kind == "type" and v not in (*TYPES, "number", "integer"):
        raise ValueError(f"'type' must be one of {sorted([*TYPES, 'number', 'integer'])}.")
    if kind == "schema":
        try:
            Draft202012Validator.check_schema(v)
        except SchemaError as e:
            raise ValueError(f"'schema' is not a valid JSON Schema: {e.message}") from e


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _has_type(value, name: str) -> bool:
    if name == "number":
        return _number(value)
    if name == "integer":
        return _number(value) and float(value).is_integer()
    if name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, TYPES[name]) and not isinstance(value, bool)


def equal(a, b) -> bool:
    """Equality that does not confuse True with 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def matches(m, value) -> tuple[bool, str]:
    """(ok, why not). `value` is what the app passed. A missing value is not passed to this function."""
    if not is_matcher(m):
        return (True, "") if equal(m, value) else (False, f"expected {m!r}, got {value!r}")
    kind = next(iter(KEYS & set(m)))
    want = m[kind]
    if kind == "any":
        return True, ""
    if kind == "approx":
        if not _number(value):
            return False, f"expected a number near {want}, got {value!r}"
        tol = max(m.get("tol", 1e-6 if "rel" not in m else 0.0), abs(want) * m.get("rel", 0.0))
        return (abs(value - want) <= tol, "" if abs(value - want) <= tol else f"expected {want} within {tol:g}, got {value!r}")
    if kind == "regex":
        ok = isinstance(value, str) and re.match(want, value) is not None
        return ok, "" if ok else f"{value!r} does not match /{want}/"
    if kind == "in":
        ok = any(equal(value, w) for w in want)
        return ok, "" if ok else f"{value!r} is not one of {want}"
    if kind == "contains":
        ok = (isinstance(value, str) and isinstance(want, str) and want in value) or (
            isinstance(value, list) and any(equal(x, want) for x in value))
        return ok, "" if ok else f"{value!r} does not contain {want!r}"
    if kind == "type":
        ok = _has_type(value, want)
        return ok, "" if ok else f"{value!r} is not of type {want}"
    errors = list(Draft202012Validator(want).iter_errors(value))
    return not errors, "" if not errors else f"schema: {errors[0].message}"


def match_args(expected: dict, actual: dict) -> list[str]:
    """Problems when the call arguments do not fit. Extra arguments in `actual` are allowed."""
    problems = []
    for key, m in expected.items():
        if key not in actual:
            problems.append(f"missing argument '{key}'")
            continue
        ok, why = matches(m, actual[key])
        if not ok:
            problems.append(f"argument '{key}': {why}")
    return problems
