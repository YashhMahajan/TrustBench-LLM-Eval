# Eval-Driven Development with TrustBench

Eval-Driven Development (EDD) means you decide if a change to an LLM system is good by **measuring it**, not by reading a few outputs.
This guide shows the loop, the commands for each step, and how to read the answers.
To see it work in two minutes, with no API key: `make edd-demo`.

## 1. The loop

```
   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
   │  1. TEST      │──▶│  2. ANALYZE   │──▶│  3. IMPROVE   │──▶│  4. DECIDE    │
   │ run the suite │   │ how did it    │   │ one change,   │   │ compare, then │
   │ on your app   │   │ fail?         │   │ one hypothesis│   │ ask the gate  │
   └───────────────┘   └───────────────┘   └───────────────┘   └───────┬───────┘
          ▲                                                            │
          │          ┌───────────────────────────────┐                 │
          └──────────│ 5. CLOSE THE LOOP             │◀────────────────┘
                     │ a production failure becomes  │
                     │ a new test case               │
                     └───────────────────────────────┘
```

| Step | Command | What you get |
|---|---|---|
| Test | `trustbench run --target v1` | Pass rates with 95% intervals. Your app is a `python:` or `http:` target. |
| Analyze | `trustbench failures RUN` | Labels (`wrong_tool`, `bad_args`, `looped`, `gave_up`, ...), recurring patterns, what to fix first |
| Improve | (you) | One change. Write the hypothesis down. |
| Decide | `trustbench experiment compare v2` and `trustbench gate check RUN --baseline PREV` | A verdict on the hypothesis, and a release decision |
| Close the loop | `trustbench traces promote`, `proposals approve`, `proposals export` | A new dataset version that keeps the old failure fixed |

## 2. Test your own app

An app target is a function (or a service) that takes the customer's message and answers:

```python
# myapp/agent.py
from trustbench.providers.recorder import tool_call, span

def answer(prompt: str, case_input: dict | None = None) -> str:     # case_input has the whole conversation
    with span("retrieval", "search_kb", docs=docs):                  # lets the rag_* checks read your documents
        ...
    tool_call("refund", {"order_id": "A1", "amount": 19.99}, result=result)   # lets tool_call and trajectory see it
    return "Refund issued."
```

```
trustbench run --model python:myapp.agent:answer@v1      # run it from your project folder
```

`@v1` is **your version label**. Change it when the behaviour changes. TrustBench stores answers, so a rerun costs nothing, and the label
(or the git commit) is part of the stored key, so an old answer is never replayed into a new version. With no label, a clean git
tree is used. With a dirty tree and no label nothing is stored.

A function may also take `system` (the system prompt of the run, from `--system-prompt-file`) so you can test prompt changes.
`python:` and `http:` targets run **your** code and URLs, so only the command line and pytest can start them. The server never does.

## 3. Write a dataset that finds real problems

One case is one conversation plus checks on **what the agent did**:

```json
{"id": "refund-over-limit-01", "task": "refund",
 "input": {"messages": [{"role": "user", "content": "Please refund order A110."}]},
 "expected": {"tool_calls": [{"name": "lookup_order", "args": {"order_id": "A110"}}, {"name": "escalate_to_human"}],
              "final_state": {"tools.refund.count": 0, "tools.escalate_to_human.count": 1}},
 "evaluation": {"evaluators": [{"type": "tool_call", "forbidden": ["refund"]},
                               {"type": "trajectory", "order": "exact", "steps": ["lookup_order", "escalate_to_human"]},
                               {"type": "task_success"}]},
 "metadata": {"difficulty": "hard", "critical": true, "positive": true}}
```

| Check | Looks at | Needs a judge model? |
|---|---|---|
| `tool_call` | which tools, with which arguments (exact value, `approx`, `regex`, `in`, `contains`, `type`, `schema`), forbidden and extra calls | no |
| `trajectory` | order of steps (`exact`, `in_order`, `any_order`, `contains`), step budget, loops | no |
| `task_success` | the end state, by dotted paths such as `tools.refund.count` or `output` | no |
| `exact_match`, `contains`, `rule`, `numeric`, `json_schema` | the text | no |
| `rag_faithfulness`, `rag_answer_relevance`, `rag_context_precision`, `rag_context_recall` | answer and retrieved documents | **yes** |
| `llm_judge`, `grounded_claims` | open-ended answers | **yes** |

**Metadata that does work:**
- `task`, `difficulty`: slices. A 90% average can hide a slice at 40%. `experiment compare` warns when a slice got worse.
- `critical: true`: one failure here blocks a release (`critical_failures_allowed: 0` in the policy).
- `positive: true`: the right action is to refuse or escalate. A failure is a *miss*. Used by the cost model.

**How many cases?** The interval is how honest the number is. For an observed 90% pass rate:

| Cases | 95% interval | Meaning |
|---|---|---|
| 20 | 70% to 97% | A first look. Cannot gate anything. |
| 50 | 79% to 96% | Catches big regressions (10+ points) |
| 100 | 83% to 95% | Reasonable for a feature |
| 400 | 87% to 93% | Can see a 3-point change |
| 1000 | 88% to 92% | Can see a 2-point change |

With few cases the gate says **inconclusive**, and by default that blocks the release. That is the point: it will not guess.
Grow the dataset from production (step 5), not by writing cases in bulk.

## 4. Read the answers

**`experiment compare`** says what happened to your hypothesis:

| Verdict | Meaning |
|---|---|
| supported | Better, and no slice got worse |
| mixed | Better overall, but a slice got clearly worse. Look at the slices. |
| refuted | Worse |
| no effect | The whole interval is inside your threshold (`--min-effect`) |
| cannot tell | Not enough cases to say |

**`gate check`** applies your `policy.yaml`: `pass`, `fail`, `inconclusive` (evidence too weak), `incomplete` (the run did not finish
or an evaluator broke). A better version can still fail the gate. In the demo v1 is *better* than v0 (+54 points) and blocked, because
it refunds amounts over the limit.

A broken evaluator is reported and left out of the pass rate. A failed call to your app counts as a failed case.

## 5. Price the mistakes

A pass rate does not say what a mistake costs. The cost model does (`policy.yaml`):

```yaml
cost_model:
  volume_per_year: 500000
  positive_rate: 0.03            # 3% of real requests need a human decision
  costs: {false_negative: 150.0, false_positive: 5.0, true_positive: 5.0}
  processing_cost_per_item: 0.002
  manual: {fp_rate: 0.01, fn_rate: 0.02, cost_per_item: 1.50}      # what you pay today
annual_cost_max_usd: 900000
```

`trustbench cost RUN` shows the miss rate and false alarm rate with intervals, and the yearly cost against the manual process and a
perfect system. The interval is wide on a small dataset, so the gate says *inconclusive* when a cost that looks fine could still be over the
limit. An unknown cost (no price, no `processing_cost_per_item`) is `incomplete`, never zero.
The formula reproduces the published expense-audit example: $380,000 for the manual process and $100,000 for a perfect system.

## 6. Make it part of your day

**A config file.** `trustbench init` writes `trustbench.yaml` (targets, dataset, policy, run settings). Then `trustbench run` needs no flags.

**pytest.** The plugin is installed with TrustBench:

```python
def test_support_agent(trustbench):
    trustbench.assert_eval("datasets/support.jsonl", "python:myapp.agent:answer@v1", min_pass=0.9)
```

A plain `pytest` **replays stored answers** (free, fast). If an answer is not stored the test fails and tells you to run `pytest --trustbench-live`
once. Live calls also need `--trustbench-live` or `TRUSTBENCH_LIVE=1`, and respect `--trustbench-budget USD`. The verdict comes from the release gate.

**CI.** The reusable Action runs your candidate and a baseline on the same cases, applies the policy, and comments on the pull request
(updating one comment, never adding many). Use it in a `pull_request` workflow:

```yaml
permissions: {contents: read, pull-requests: write}
steps:
  - uses: actions/checkout@v4
  - uses: YashhMahajan/TrustBench-LLM-Eval@<a full commit SHA>
    with: {target: candidate, baseline-target: main}
```

The step that runs your app gets no token. A separate step, which runs only TrustBench's own poster, holds it. Exit code 1 blocks the merge. The same logic runs on your machine: `trustbench ci check --target candidate --baseline-target main`.
Never use `pull_request_target` with this Action. It would run code from the pull request with write access.

**Experiments.** `trustbench experiment record v2 RUN --parent v1 --hypothesis "..."` keeps the history. `trustbench experiment list` is the
story of a feature: what you tried, why, and what the evidence said.

## 7. Close the loop from production

```python
from trustbench.sdk import TrustBench
tb = TrustBench()
with tb.trace("support-bot", model="...") as t:
    t.input = question
    t.output = answer
```

Flagged traces (errors, thumbs down, failed online checks) appear in `trustbench traces flagged`. `traces promote TRACE --answer "..."` proposes a test,
a person approves it (`proposals approve`), and `proposals export` writes a new dataset version. Nothing becomes a test without a person.
Text is stored only if the workspace allows it (`workspace set --store-content`), after redaction.

## 8. Pitfalls

- **Changing code without changing the label.** With `@v1` kept and the code changed, you replay old answers. Change the label, or use `--no-cache`.
- **Judge calls are not stored.** A test that uses `rag_*` or `llm_judge` needs the judge's key even in replay mode. Prefer the rule-based checks where you can.
- **A policy tighter than the data.** `maximum_regression: 0.05` with 50 cases can only ever say "inconclusive".
- **Mixing up `positive`.** It marks cases where the right action is to refuse or escalate, not cases that "should pass".
- **Trusting a judge you did not validate.** Run `trustbench validate-judge` against human labels first.
