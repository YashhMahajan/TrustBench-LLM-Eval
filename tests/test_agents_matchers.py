"""How a test case describes an expected value."""

import pytest

from trustbench.agents.matchers import equal, is_matcher, match_args, matches, validate_matcher


@pytest.mark.parametrize("m,value,ok", [
    ("A1", "A1", True), ("A1", "a1", False),
    (5, 5.0, True), (True, 1, False), (1, True, False), (False, 0, False),
    ({"a": 1}, {"a": 1}, True), ({"a": 1}, {"a": 2}, False), ([1, 2], [1, 2], True), ([1, 2], [2, 1], False),
    ({"approx": 10.0, "tol": 0.01}, 10.005, True), ({"approx": 10.0, "tol": 0.01}, 10.02, False),
    ({"approx": 100, "rel": 0.01}, 100.9, True), ({"approx": 100, "rel": 0.01}, 102, False),
    ({"approx": 1.0}, 1.0000001, True), ({"approx": 1.0}, "1.0", False), ({"approx": 1.0}, True, False),
    ({"regex": r"^A\d+$"}, "A12", True), ({"regex": r"^A\d+$"}, "B12", False), ({"regex": "x"}, 5, False),
    ({"in": ["a", "b"]}, "b", True), ({"in": ["a", "b"]}, "c", False), ({"in": [1]}, True, False),
    ({"contains": "refund"}, "full refund now", True), ({"contains": "refund"}, "nothing", False),
    ({"contains": 2}, [1, 2, 3], True), ({"contains": 9}, [1, 2], False),
    ({"type": "string"}, "x", True), ({"type": "string"}, 1, False),
    ({"type": "number"}, 1.5, True), ({"type": "number"}, True, False),
    ({"type": "integer"}, 3, True), ({"type": "integer"}, 3.0, True), ({"type": "integer"}, 3.5, False),
    ({"type": "boolean"}, False, True), ({"type": "boolean"}, 0, False),
    ({"type": "array"}, [], True), ({"type": "object"}, {}, True), ({"type": "array"}, "x", False),
    ({"schema": {"type": "integer", "minimum": 1}}, 2, True), ({"schema": {"type": "integer", "minimum": 1}}, 0, False),
    ({"any": True}, None, True),
])
def test_matches(m, value, ok):
    assert matches(m, value)[0] is ok


def test_a_failed_match_says_why():
    assert "expected 'A1'" in matches("A1", "B")[1]
    assert "within" in matches({"approx": 1, "tol": 0.1}, 2)[1]


def test_a_dict_without_a_matcher_key_is_a_plain_value():
    assert not is_matcher({"order": 1}) and is_matcher({"regex": "x"}) and not is_matcher({})


@pytest.mark.parametrize("bad,needle", [
    ({"regex": "("}, "valid pattern"), ({"approx": "x"}, "number"), ({"approx": True}, "number"),
    ({"approx": 1, "tol": -1}, "not negative"), ({"in": "abc"}, "list"), ({"type": "banana"}, "'type' must be"),
    ({"schema": {"type": "nope"}}, "valid JSON Schema"), ({"regex": "x", "in": ["a"]}, "exactly one"),
    ({"regex": "x", "tol": 1}, "does not take"),
])
def test_bad_matchers_are_rejected(bad, needle):
    with pytest.raises(ValueError, match=needle):
        validate_matcher(bad)


def test_valid_matchers_and_plain_values_pass_validation():
    for ok in ("x", 1, {"a": 1}, {"approx": 1, "tol": 0.1}, {"approx": 1, "rel": 0.1}, {"any": True}, {"type": "integer"}):
        validate_matcher(ok)


def test_equal_does_not_confuse_booleans_with_numbers():
    assert equal(1, 1.0) and not equal(True, 1) and not equal({"a": True}, {"a": 1}) and not equal([True], [1])


def test_match_args_reports_missing_and_wrong_arguments_but_allows_extras():
    assert match_args({"id": "A", "n": {"type": "integer"}}, {"id": "A", "n": 2, "extra": 1}) == []
    problems = match_args({"id": "A", "n": 1}, {"id": "B"})
    assert any("argument 'id'" in p for p in problems) and "missing argument 'n'" in problems
