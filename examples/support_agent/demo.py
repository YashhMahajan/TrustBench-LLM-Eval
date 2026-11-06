"""The Eval-Driven Development story, with real TrustBench commands. No API key needed.   Run: make edd-demo

  1. v0 is the simplest thing that could work. We measure it. It is bad. The failures say how.
  2. v1 fixes the tool use. It is better, and the evidence says so. But the release gate blocks it.
  3. v2 adds a knowledge base and a human escalation. The gate lets it ship.
  4. Production finds a gap that no test covered. We turn it into a test and fix it (v3).

Everything runs in a temporary database. Nothing is left behind.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRUSTBENCH = str(Path(sys.executable).parent / "trustbench")
TMP = Path(tempfile.mkdtemp(prefix="trustbench-demo-"))
os.environ.update({"TRUSTBENCH_DATABASE_URL": f"sqlite:///{TMP}/demo.db"})
os.environ["TRUSTBENCH_SECRET_KEY"] = subprocess.run(  # noqa: S603 - our own command, fixed arguments
    [TRUSTBENCH, "secrets", "generate-key"], capture_output=True, text=True).stdout.splitlines()[0]
DATASET = "datasets/support.jsonl"


def say(text: str = "") -> None:
    print(text, flush=True)


def step(title: str, why: str) -> None:
    say(f"\n{'=' * 78}\n{title}\n{why}\n{'=' * 78}")


def tb(*args: str, expect: int | None = 0) -> int:
    """Run a trustbench command in this folder. Show it, run it, and check the exit code the story needs."""
    say(f"\n$ trustbench {' '.join(args)}")
    code = subprocess.run([TRUSTBENCH, *args], cwd=HERE).returncode  # noqa: S603 - our own command
    if expect is not None and code != expect:
        sys.exit(f"\nThe story broke: 'trustbench {' '.join(args)}' exited with {code}, not {expect}.")
    return code


def newest_run() -> str:
    from trustbench.cli_common import get_factory
    from trustbench.db import EvaluationRun
    with get_factory()() as s:
        return s.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).first().id


def run(target: str, *extra: str) -> str:
    tb("run", "--target", target, *extra)
    return newest_run()


def main() -> None:
    step("1. Measure the baseline (v0)", "The simplest agent: it answers from memory and calls its tools with wrong arguments.")
    r0 = run("v0")
    tb("failures", r0)
    tb("experiment", "record", "v0", r0, "--hypothesis", "Baseline: the simplest thing that could work")

    step("2. Fix the tool use (v1)", "Hypothesis: calling the tools correctly fixes the bad arguments. Is that true? And is v1 ready to ship?")
    r1 = run("v1")
    tb("experiment", "record", "v1", r1, "--parent", "v0", "--hypothesis", "Correct tool use fixes the bad arguments")
    tb("experiment", "compare", "v1")
    say("\nThe change is real. But a better agent is not always a safe one. Ask the release gate:")
    tb("gate", "check", r1, "--baseline", r0, expect=1)
    tb("cost", r1)

    step("3. Add a knowledge base and a human escalation (v2)", "Hypothesis: searching the knowledge base fixes policy answers, and escalating large refunds removes the critical failures.")
    r2 = run("v2")
    tb("experiment", "record", "v2", r2, "--parent", "v1", "--hypothesis", "A knowledge base and a human escalation fix policy answers and critical failures")
    tb("experiment", "compare", "v2")
    tb("gate", "check", r2, "--baseline", r1)
    tb("cost", r2)

    step("4. Production finds a gap (v3)", "A customer wrote: 'My parcel arrived damaged'. v2 did not understand and the customer clicked thumbs down. No test covered it.")
    from trustbench.cli_common import get_factory
    from trustbench.tenancy import ensure_default_workspace
    from trustbench.traces import TraceIn, ingest
    tb("workspace", "set", "default", "--store-content")
    ingest(get_factory(), ensure_default_workspace(get_factory()).id, [TraceIn(
        trace_id="t-damaged-001", name="support", input="My parcel arrived damaged, order A100.", output="How can I help?",
        feedback=-1, model="python:support_agent.v2:answer@v2")])
    tb("traces", "flagged")
    say("\nA person gives the right answer, the trace becomes a proposed test, and a person approves it:")
    tb("traces", "promote", "t-damaged-001", "--answer", "refunded", "--dataset", DATASET)
    tb("proposals", "approve", "1", "--checked-answers")
    grown = str(TMP / "support_v2.jsonl")
    tb("proposals", "export", DATASET, "--out", grown)
    say("\nThe new test is now part of the dataset. v2 fails it, as it should:")
    r2b = run("v2", "--dataset", grown)
    tb("failures", r2b)
    say("\nv3 understands 'arrived damaged'. The old failure stays fixed:")
    r3 = run("v3", "--dataset", grown)
    tb("experiment", "record", "v3", r3, "--parent", "v2", "--hypothesis", "Production showed that 'arrived damaged' is not understood")
    tb("gate", "check", r3, "--baseline", r2b)

    step("The whole history", "Every variant, its hypothesis and its pass rate. This is what you show a team, or an interviewer.")
    tb("experiment", "list")
    say(f"\nDone. The temporary database ({TMP}) can be deleted.")
    summary = {"v0": r0, "v1": r1, "v2": r2, "v3": r3}
    (TMP / "runs.json").write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
