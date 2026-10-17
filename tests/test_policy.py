import pytest

from trustbench.policy import TEMPLATE, PolicyError, ReleasePolicy, load_policy, parse_policy


def test_template_is_valid_and_complete():
    p = parse_policy(TEMPLATE)
    assert p.name == "production" and p.overall.minimum_score == 0.90 and p.overall.maximum_regression == 0.01
    assert p.dimensions["grounded_qa"].min_cases == 20 and p.latency_p95_max_seconds == 5.0
    assert p.on_insufficient_evidence == "block" and p.critical_failures_allowed == 0


def test_defaults_are_safe():
    p = ReleasePolicy()
    assert p.on_insufficient_evidence == "block" and p.confidence_bound and p.critical_failures_allowed == 0


def test_works_with_or_without_the_top_key():
    assert parse_policy("name: a\nmin_cases: 10").min_cases == 10
    assert parse_policy("release_policy:\n  name: b").name == "b"
    assert parse_policy("").name == "default"


@pytest.mark.parametrize("text,fragment", [
    ("release_policy:\n  min_casez: 5", "min_casez"),
    ("overall:\n  minimum_score: 1.5", "overall.minimum_score"),
    ("on_insufficient_evidence: maybe", "on_insufficient_evidence"),
    ("dimensions:\n  qa:\n    minimum: 0.9", "dimensions.qa.minimum"),
    ("min_cases: 0", "min_cases"),
    ("latency_p95_max_seconds: -1", "latency_p95_max_seconds"),
])
def test_bad_policies_name_the_problem(text, fragment):
    with pytest.raises(PolicyError, match=fragment.replace(".", r"\.")):
        parse_policy(text)


def test_invalid_yaml_and_wrong_shape():
    with pytest.raises(PolicyError, match="not valid YAML"):
        parse_policy("a: [")
    with pytest.raises(PolicyError, match="mapping"):
        parse_policy("- 1\n- 2")


def test_load_from_file(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(TEMPLATE)
    assert load_policy(f).name == "production"
    with pytest.raises(PolicyError, match="policy init"):
        load_policy(tmp_path / "missing.yaml")
