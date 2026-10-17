import asyncio

from helpers import FnProvider
from trustbench.judge import Judge
from trustbench.pairwise import compare, inconsistency_rate, win_rate_a


def judge(fn):
    return Judge(FnProvider(fn), "j")


def reply(winner):
    return f'{{"winner": "{winner}", "rationale": "r"}}'


def prefers(text):
    """A judge that likes one answer, wherever it is placed."""
    def fn(prompt):
        first = prompt.split("<first_answer>\n")[1].split("\n</first_answer>")[0]
        return reply("first" if first == text else "second")
    return fn


def test_consistent_judge_picks_the_better_answer_in_both_orders():
    v = asyncio.run(compare(judge(prefers("good")), "q", "good", "bad"))
    assert (v.winner, v.consistent) == ("A", True)
    v = asyncio.run(compare(judge(prefers("good")), "q", "bad", "good"))
    assert (v.winner, v.consistent) == ("B", True)


def test_position_biased_judge_gives_inconsistent_tie():
    v = asyncio.run(compare(judge(lambda p: reply("first")), "q", "x", "y"))
    assert (v.winner, v.consistent) == ("tie", False)


def test_explicit_tie_is_consistent():
    v = asyncio.run(compare(judge(lambda p: reply("tie")), "q", "x", "y"))
    assert (v.winner, v.consistent) == ("tie", True)


def test_two_calls_per_pair_and_rates():
    j = judge(prefers("good"))
    verdicts = [asyncio.run(compare(j, "q", "good", "bad")), asyncio.run(compare(judge(lambda p: reply("first")), "q", "x", "y"))]
    assert len(j.provider.prompts) == 2
    assert win_rate_a(verdicts) == 0.75 and inconsistency_rate(verdicts) == 0.5
    assert win_rate_a([]) is None
