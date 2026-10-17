import random

from trustbench.rundata import Record
from trustbench.slices import MIN_SLICE, find_slices, length_bucket


def make(n_per_task, pass_rates, seed=0):
    rng = random.Random(seed)
    records = []
    for task, rate in pass_rates.items():
        for i in range(n_per_task):
            records.append(Record(case_id=f"{task}{i}", task=task, passed=rng.random() < rate,
                                  metadata={"difficulty": "easy" if i % 2 else "hard"}, input_words=10))
    return records


def test_a_truly_weak_task_is_found_first():
    recs = make(60, {"math": 0.4, "qa": 0.9, "chat": 0.9, "code": 0.9})
    slices = find_slices(recs)
    assert slices[0].dimension == "task" and slices[0].value == "math" and slices[0].weak
    assert slices[0].score.n == 60 and slices[0].rest_rate > 0.8
    assert [s.value for s in slices if s.weak] == ["math"]


def test_equal_groups_are_not_flagged():
    flagged = 0
    for seed in range(20):
        recs = make(40, {f"t{i}": 0.8 for i in range(8)}, seed)   # Eight tasks, same true rate.
        flagged += any(s.weak for s in find_slices(recs))
    assert flagged <= 3   # Without correction, nearly every run would flag something.


def test_small_slices_are_listed_but_not_tested():
    recs = make(40, {"big": 0.9}) + [Record(case_id=f"s{i}", task="tiny", passed=False) for i in range(MIN_SLICE - 1)]
    tiny = next(s for s in find_slices(recs) if s.value == "tiny")
    assert tiny.underpowered and tiny.p_value is None and not tiny.weak and tiny.score.n == MIN_SLICE - 1


def test_metadata_and_length_slices_exist():
    recs = make(30, {"a": 0.9, "b": 0.9})
    for i, r in enumerate(recs):
        r.input_words = 5 if i % 3 else 300   # Two length groups.
    dims = {s.dimension for s in find_slices(recs)}
    assert {"task", "difficulty", "input length"} <= dims
    assert length_bucket(5).startswith("short") and length_bucket(50).startswith("medium") and length_bucket(500).startswith("long")


def test_metadata_slice_finds_hard_cases():
    rng = random.Random(1)
    recs = [Record(case_id=f"c{i}", task="qa", passed=(rng.random() < (0.95 if i % 2 else 0.35)),
                   metadata={"difficulty": "easy" if i % 2 else "hard"}) for i in range(200)]
    weak = [s for s in find_slices(recs) if s.weak]
    assert [(s.dimension, s.value) for s in weak] == [("difficulty", "hard")]


def test_skipped_and_failed_calls_are_handled():
    recs = make(30, {"a": 0.9, "b": 0.9}) + [Record(case_id="x", task="a", status="skipped")]
    recs += [Record(case_id=f"f{i}", task="b", status="failed") for i in range(10)]
    slices = find_slices(recs)
    b = next(s for s in slices if s.value == "b")
    assert b.score.n == 40 and b.rest_rate > b.score.rate   # Failed calls count against the model.


def test_repetition_scores_use_the_permutation_path():
    recs = [Record(case_id=f"a{i}", repetition=r, task="a", passed=r == 0) for i in range(30) for r in range(2)]
    recs += [Record(case_id=f"b{i}", repetition=r, task="b", passed=True) for i in range(30) for r in range(2)]
    assert [s.value for s in find_slices(recs) if s.weak] == ["a"]
