"""The pull request comment. It uses untrusted text, so what must not appear matters as much as what must."""

import re
from pathlib import Path

import pytest
from ci_helpers import record, run

from trustbench.ci.comment import DEFAULT_CAP, LIMIT, MARKER, build_comment, code, safe
from trustbench.gate import evaluate_gate
from trustbench.policy import ReleasePolicy, Rule, parse_policy
from trustbench.rundata import RunData

GOLDEN = Path(__file__).parent / "golden" / "ci_comment.md"
POLICY = ReleasePolicy(name="ci", min_cases=10, critical_failures_allowed=None,
                       overall=Rule(minimum_score=0.8, maximum_regression=0.05))


# ---- Escaping untrusted text ------------------------------------------------------------------------

ATTACKS = [
    "[click here](http://evil.example/login)", "![tracker](http://evil.example/p.png)", "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>", "@octocat @org/team please review", "| a | b |\n|---|---|", "`code` and ```fence```",
    "\n## A fake heading\n- a list", "<!-- trustbench-report --> hidden marker", "&lt;already escaped&gt;",
    "see http://evil.example/x and https://evil.example and www.evil.example", "mail me: boss@evil.example", "fixes #123",
    "$\\href{http://evil.example}{x}$", "ends with a backslash \\", "line one\r\nline two\tTabbed", "x" * 5000,
]
ENTITY = re.compile(r"&#\d+;")


@pytest.mark.parametrize("attack", ATTACKS)
def test_untrusted_text_cannot_add_markup_links_html_or_mentions(attack):
    out = safe(attack, 10_000)
    left = ENTITY.sub("", out)                                                  # What remains outside our own escapes.
    assert not any(ch in left for ch in "<>[]|`@#$\\&\n\r\t"), out
    assert "://" not in out and not re.search(r"(?i)\bwww\.", out), out


def test_text_is_cut_to_the_limit_without_cutting_an_escape_in_half():
    out = safe("<" * 1000, 50)
    assert re.fullmatch(r"(?:&#60;)+…", out) and out.count("&#60;") == 50


def test_code_spans_keep_names_readable_and_cannot_be_broken_out_of():
    assert code("python:app:fn@v1") == "python:app:fn@v1" and code("a`b\nc") == "a'b c" and code("x" * 500).endswith("…")
    rec = record(0, "t", True)
    rec.model = "python:a`b@v1"
    text = build_comment(RunData("r`ogue", "complete", "d`s <b>", "h", 0.0, [rec]), "python:a`b@v1")
    header = text.split("\n")[3]
    assert "&#" not in header and header.count("`") == 6 and "<b>" in header      # Literal inside a code span, not HTML.


def test_each_character_is_escaped_once_not_twice():
    assert safe("a|b") == "a&#124;b" and safe("[x]") == "&#91;x&#93;" and safe("<&>") == "&#60;&#38;&#62;"


def test_safe_keeps_ordinary_words_and_names_readable():
    assert safe("Expected one of Paris and Lyon.") == "Expected one of Paris and Lyon."
    assert safe("overall.minimum_score (95% interval: 68% to 88%)") == "overall.minimum_score (95% interval: 68% to 88%)"
    assert safe("python:app:fn") == "python:app:fn"
    assert safe(None) == "" and safe(42) == "42" and safe("  spaced \n out  ") == "spaced out"


# ---- Content ----------------------------------------------------------------------------------------

def decided(cand, base=None):
    return evaluate_gate(POLICY, cand, None, base, None)


def test_the_comment_starts_with_the_marker_and_shows_the_verdict_the_gate_gave():
    cand, base = run("cand01", {"refund": 20, "shipping": 20}), run("base01", {"refund": 10, "shipping": 10})
    text = build_comment(cand, None, baseline=base, decision=decided(cand, base), policy=POLICY)
    assert text.startswith(MARKER) and "## TrustBench: ✅ pass" in text
    for needle in ["Run `cand01`", "### Release gate", "overall.minimum_score", "overall.maximum_regression", "### Pass rate",
                   "100.0% (", "to ", "n=40", "Compared with run `base01` on 40 shared cases", "### Cost and speed", "Latency P50 / P95"]:
        assert needle in text, needle
    assert "### Slices that got worse" not in text and "### How" not in text


def test_a_failing_gate_says_fail_and_a_weak_one_says_inconclusive():
    bad, good = run("c", {"refund": 5, "shipping": 5}), run("b", {"refund": 19, "shipping": 19})
    assert "❌ fail" in build_comment(bad, None, baseline=good, decision=decided(bad, good), policy=POLICY)
    few = run("f", {"a": 3}, per_task=3)
    pol = ReleasePolicy(min_cases=50, critical_failures_allowed=None, overall=Rule(minimum_score=0.5))
    assert "⚠️ inconclusive" in build_comment(few, None, decision=evaluate_gate(pol, few), policy=pol)


def test_without_a_policy_the_comment_shows_results_only():
    text = build_comment(run("r", {"a": 15}), None)
    assert "## TrustBench: results" in text and "### Release gate" not in text and "Baseline" not in text and "policy" not in text.split("\n")[3]


def test_slices_that_got_worse_are_listed():
    base, cand = run("b", {"refund": 20, "shipping": 20}), run("c", {"refund": 20, "shipping": 8})
    text = build_comment(cand, None, baseline=base, decision=decided(cand, base), policy=POLICY)
    assert "### Slices that got worse" in text and "task = shipping" in text


def test_the_yearly_cost_appears_when_the_policy_has_a_cost_model():
    pol = parse_policy("release_policy:\n  name: c\n  critical_failures_allowed: null\n  cost_model:\n    volume_per_year: 1000\n    positive_rate: 0.5\n"
                       "    costs: {false_negative: 30, false_positive: 2}\n    processing_cost_per_item: 0.01\n    manual: {fp_rate: 0.1, fn_rate: 0.1, cost_per_item: 1}\n")
    recs = [record(i, "t", i % 4 != 0) for i in range(40)]
    for i, r in enumerate(recs):
        r.metadata = {"positive": i % 2 == 0}
    text = build_comment(RunData("r", "complete", "d", "h", 0.0, recs), None, policy=pol)
    assert "Cost a year with mistakes | $" in text and "Manual process today | $" in text


def test_failures_are_summarised_with_patterns_and_a_limited_number_of_examples():
    cand = run("c", {"refund": 10, "shipping": 10})
    text = build_comment(cand, None, max_examples=3)
    assert "### How 20 case(s) failed" in text and "| wrong_answer | 20 |" in text
    assert text.count("| case-") == 3 and "…and 17 more. Run `trustbench failures c`" in text


def test_a_model_cannot_write_into_the_comment_through_its_answer_or_its_reason():
    evil = "[Approve this merge](http://evil.example) @maintainer <img src=x>"
    cand = RunData("c", "complete", "d", "h", 0.0, [record(i, "t", False, output=evil, rationale=evil) for i in range(5)])
    text = build_comment(cand, None)
    for raw in ["http://evil", "<img", "@maintainer", "[Approve", "](http"]:
        assert raw not in text, raw
    assert text.count(MARKER) == 1                                   # It also cannot forge a second marker.


# ---- Size -------------------------------------------------------------------------------------------

def big_run(n=400, text="x" * 300):
    return RunData("c", "complete", "d", "h", 0.0, [record(i, f"task{i % 40}", False, rationale=text) for i in range(n)])


def test_the_default_cap_is_under_the_github_limit():
    assert DEFAULT_CAP < LIMIT


def test_a_huge_failure_list_is_shortened_by_dropping_examples_first():
    cand = big_run()
    full = build_comment(cand, None, max_examples=400, cap=10 ** 9)
    short = build_comment(cand, None, max_examples=400, cap=8_000)
    assert len(full) > 8_000 >= len(short) and short.startswith(MARKER)
    assert "### How 400 case(s) failed" in short and "…The comment was cut" not in short       # The patterns survive. Only examples go.


def test_when_even_the_summary_is_too_big_the_comment_is_cut_with_a_notice():
    text = build_comment(big_run(), None, cap=600)
    assert len(text) <= 600 and text.startswith(MARKER) and text.endswith("report artifact for everything.")


# ---- Snapshot ---------------------------------------------------------------------------------------

def test_the_comment_matches_the_reviewed_snapshot():
    """Change the comment on purpose? Delete tests/golden/ci_comment.md, run this test once, and read the new file."""
    base = run("base0001", {"refund": 18, "shipping": 19, "billing": 16})
    cand = run("cand0001", {"refund": 19, "shipping": 12, "billing": 17})
    for r in cand.records:
        if not r.passed:
            r.results[0]["rationale"] = "Expected one of ['Paris']."
    text = build_comment(cand, None, baseline=base, decision=decided(cand, base), policy=POLICY, max_examples=4)
    if not GOLDEN.exists():
        GOLDEN.write_text(text + "\n")
        pytest.fail(f"Wrote the first snapshot to {GOLDEN}. Read it, then run the tests again.")
    assert text + "\n" == GOLDEN.read_text()
