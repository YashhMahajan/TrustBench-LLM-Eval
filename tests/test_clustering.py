import pytest

from trustbench.clustering import TfidfEmbedder, cluster_failures, cluster_vectors, cosine, tokens
from trustbench.failures import Failure
from trustbench.rundata import Record


def fail(cid, question, output="", labels=("wrong_answer",), task="qa", model="m:m"):
    rec = Record(case_id=cid, task=task, input={"prompt": question}, output=output)
    return Failure(cid, model, task, list(labels), [], output, record=rec)


MATH = [fail(f"m{i}", f"What is {a} multiplied by {b}? Give the number.", str(a * b + 1), task="math")
        for i, (a, b) in enumerate([(7, 8), (12, 9), (13, 6), (15, 4)])]
DATES = [fail(f"d{i}", f"In which year did the {e} happen? Give the year.", "1900", task="history")
         for i, e in enumerate(["treaty signing", "great fire", "royal wedding", "moon landing"])]
FORMAT = [fail(f"f{i}", f"Extract the order {i} as JSON.", "Sure! Here is the order", ("format_violation",), "structured")
          for i in range(3)]


def test_tokens_drop_stop_words_and_add_pairs():
    t = tokens("What is the capital of France?")
    assert "capital" in t and "france" in t and "capital france" in t and "the" not in t
    assert tokens("buy 12 apples and 7 pears")[:3] == ["buy", "num", "apples"]   # Numbers are details of one case.


def test_similar_texts_have_high_cosine_and_different_texts_low():
    v = TfidfEmbedder().embed(["multiply seven by eight", "multiply twelve by nine", "royal wedding year history"])
    assert cosine(v[0], v[1]) > cosine(v[0], v[2])
    assert cosine(v[0], v[0]) == pytest.approx(1.0)


def test_three_different_failure_patterns_make_three_clusters():
    clusters, loose = cluster_failures(MATH + DATES + FORMAT, total_cases=40)
    assert loose == []
    groups = [{m.case_id[0] for m in c.members} for c in clusters]
    assert sorted(map(sorted, groups)) == [["d"], ["f"], ["m"]]
    assert [c.size for c in clusters] == [4, 4, 3]


def test_cluster_details_are_useful():
    clusters, _ = cluster_failures(MATH + DATES + FORMAT, total_cases=40)
    fmt = next(c for c in clusters if c.tasks == {"structured": 3})
    assert fmt.labels == {"format_violation": 3} and fmt.share_of_cases == pytest.approx(3 / 40)
    assert 0.3 < fmt.cohesion <= 1.0 and len(fmt.representatives) == 3
    assert fmt.follow_up.startswith("Add more test cases about") and "format_violation" in fmt.follow_up
    assert fmt.models == {"m:m": 3}


def test_the_result_does_not_depend_on_input_order():
    a, _ = cluster_failures(MATH + DATES + FORMAT, 40)
    b, _ = cluster_failures(list(reversed(MATH + DATES + FORMAT)), 40)
    key = lambda cs: sorted(sorted(m.case_id for m in c.members) for c in cs)
    assert key(a) == key(b)


def test_one_off_failures_are_left_unclustered():
    odd = fail("odd", "Translate the poem into Latin", "gibberish", task="translation")
    clusters, loose = cluster_failures(MATH + [odd], 20)
    assert [f.case_id for f in loose] == ["odd"] and len(clusters) == 1


def test_threshold_controls_how_much_is_merged():
    vectors = TfidfEmbedder().embed([f"{x}" for x in ["alpha beta gamma", "alpha beta delta", "alpha epsilon zeta"]])
    assert len(cluster_vectors(vectors, 0.9)) == 3
    assert len(cluster_vectors(vectors, 0.05)) == 1


def test_evaluator_problems_are_not_clustered_and_empty_input_is_fine():
    broken = fail("e", "q", labels=("evaluation_error",))
    assert cluster_failures([broken], 5) == ([], [])
    assert cluster_failures([], 5) == ([], [])


def test_a_custom_embedder_can_replace_the_default():
    class ByTask:
        def embed(self, texts):
            return [{"x": 1.0} if "math" in t else {"y": 1.0} for t in texts]
    clusters, _ = cluster_failures(MATH + DATES, 20, embedder=ByTask(), threshold=0.5)
    assert sorted(c.size for c in clusters) == [4, 4]


def test_failure_type_counts_in_the_text_that_is_clustered():
    from trustbench.clustering import failure_text
    f = fail("a", "q", labels=("wrong_answer", "format_violation"))
    words = failure_text(f).split()
    assert words.count("wrong_answer") == 3 and words.count("format_violation") == 3   # The text repeats the labels as separate words.
