"""Fake runs for the CI tests. No database: the comment is a pure function of RunData."""

from trustbench.rundata import Record, RunData


def result(passed: bool, evaluator: str = "exact_match", rationale: str = "", **details) -> dict:
    return {"evaluator": evaluator, "passed": passed, "score": float(passed), "details": details,
            "rationale": rationale or ("Match." if passed else "Expected one of ['Paris'].")}


def record(i: int, task: str, passed: bool, output: str = "an answer", rationale: str = "", cost: float | None = 0.001,
           evaluator: str = "exact_match", **details) -> Record:
    return Record(case_id=f"case-{i:03d}", model="python:app:fn@v1", task=task, passed=passed, latency_ms=1200.0 + i, cost_usd=cost,
                  output=output, results=[result(passed, evaluator, rationale, **details)])


def run(run_id: str, passes: dict[str, int], per_task: int = 20, **kw) -> RunData:
    """A run with `per_task` cases in each task. passes = {task: how many of its cases pass}."""
    records, n = [], 0
    for task, ok in passes.items():
        for j in range(per_task):
            records.append(record(n, task, j < ok, **kw))
            n += 1
    return RunData(run_id, "complete", "support", "abc123def", 0.0, records)
