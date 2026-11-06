# TrustBench — Product Requirements

Status: living document. Last updated with Phase 8, milestone 6 (done).
Style: short sentences. One idea in each sentence.

## 1. Problem

Teams ship LLM features by trial and error. They change a prompt, read a few outputs, and decide it looks better.
This is slow. It does not find regressions. It cannot show progress to anyone else.

Most evaluation tools have one of three limits:
- They score a bare model on a prompt. They do not test the team's own app or agent.
- They report an average. They do not say whether a change is real or noise.
- They are a notebook or a script. A team cannot share them, gate a release on them, or run them in CI.

## 2. Vision

TrustBench is the place where an engineer asks one question: "Is this change safe to ship?"
It answers with evidence: pass rates with intervals, the cases behind every number, and a clear verdict.

Eval-Driven Development (EDD) is the way of working that TrustBench supports:

1. **Test:** run the system against an evaluation suite.
2. **Analyze:** find the failure modes.
3. **Improve:** make one targeted change.
4. **Repeat:** run again. Keep the change only if the evidence says it helped.
5. **Close the loop:** a production failure becomes a new test case.

## 3. Users

| User | Goal | What they need from us |
|---|---|---|
| Application engineer | Change a prompt, model or tool and know if it broke anything | One command. A verdict in minutes. A PR comment. |
| Platform or ML engineer | Keep quality steady across many teams | Shared datasets, baselines, a release policy, CI gate |
| Engineering lead or PM | Decide if a model or version is ready | A readiness view. Cost and quality side by side. |
| Security or risk reviewer | Know that the system resists attacks and leaks nothing | Injection tests, redaction, audit log, clear limits |

## 4. Goals and non-goals

### Goals
- G1. Evaluate the user's own code (a function, a service, an agent), not only a model.
- G2. Give statistically honest answers. Say "cannot tell" when the data is too weak.
- G3. Fit into daily work: a config file, a pytest plugin, a CI action.
- G4. Turn failures into tests. Keep old failures fixed.
- G5. Be easy to read and extend. One concern in each file. We document the extension points.
- G6. Prove its own measurements. `trustbench selftest` must pass. We must also see each check fail on broken code.

### Non-goals
- Training or fine-tuning models.
- Multimodal evaluation.
- Hosting model inference.
- A hosted SaaS in this phase (the server and workspaces exist, but we do not run a service).

### Scope rule
If a part does not help the user get a correct evaluation result, we do not build it.
Use this rule to cut features, and to refuse polish that hides a weak result.

## 5. Requirements

Priority: P0 = must have. P1 = should have. P2 = later.

### 5.1 Evaluate real systems (Phase 8)
| ID | Requirement | Priority | Status |
|---|---|---|---|
| R1 | Test a Python function or an HTTP service as a target (`python:` and `http:`) | P0 | Done (M1) |
| R2 | Capture tool calls and spans while the app runs. Store them. Replay them from the cache. | P0 | Done (M1) |
| R3 | A cache key that follows the code version. No stale answers in an experiment. | P0 | Done (M1) |
| R4 | The server never runs user code | P0 | Done (M1) |
| R5 | Score tool calls: name, arguments (exact, tolerance, regex, schema), forbidden and extra calls | P0 | Done (M2) |
| R6 | Score trajectory: order, step budget, loop detection | P0 | Done (M2) |
| R7 | Score the end state in code (`task_success`) | P0 | Done (M2) |
| R8 | RAG checks: faithfulness, answer relevance, context precision, context recall | P1 | Done (M2) |
| R9 | Conversations as inputs. Bad agent fields fail when the dataset loads. | P0 | Done (M2) |
| R10 | Failure labels for agents (wrong_tool, bad_args, looped, gave_up, ...) | P1 | Done (M2) |

### 5.2 Fit into the workflow (Phase 8)
| ID | Requirement | Priority | Status |
|---|---|---|---|
| R11 | `trustbench.yaml` and `trustbench init`. `trustbench run` reads it. | P0 | Done (M3) |
| R12 | A pytest plugin. Replay from the cache by default. Live calls only on request. A session budget. | P0 | Done (M3) |
| R13 | Evaluator plugins through entry points. A broken plugin never stops TrustBench. | P1 | Done (M3) |
| R14 | Experiments: name a variant, state a hypothesis, compare with a parent, per-slice change | P0 | Done (M4) |
| R15 | A business cost model with an interval. A gate rule on yearly cost. An unknown cost is never zero. The report says incomplete. The gate says inconclusive. | P0 | Done (M4) |
| R16 | Prompt versions in the run snapshot. `compare` shows the diff. | P1 | Done (M4) |
| R17 | CI workflow for this repo and a reusable GitHub Action that comments on pull requests | P0 | Done (M5, not yet run on GitHub) |

### 5.3 Showcase and onboarding (Phase 8)
| ID | Requirement | Priority | Status |
|---|---|---|---|
| R18 | A support-agent example that runs with no API keys (`make edd-demo`) and tells the EDD story | P0 | Done (M6) |
| R19 | `docs/EDD.md` and a README section "EDD in 5 minutes" | P0 | Done (M6) |
| R20 | Dashboard: an Experiments page and a trajectory view | P1 | Done (M6) |

### 5.4 Platform (Phases 1–7, done)
- Statistical comparison and release gate.
- Judge validation and bias tests.
- Robustness and calibration.
- Failure analysis and priority.
- Production traces, redaction, drift and online evaluation.
- Multi-tenant keys and a dashboard.
- A self-test of 33 checks.

`README.md` lists every command.

## 6. Success measures

| Measure | Target |
|---|---|
| Time from `pip install` to a first verdict on the demo | Under 5 minutes, no API key |
| Time to add the gate to a pull request | Under 15 minutes |
| Tests | All pass. Each new evaluator has a test that breaks the code it watches. |
| Selftest | Exit code 0 |
| A regression in the demo (v0) | The gate blocks it (exit code 1). The fixed version (v2) passes. |
| A new engineer finds the right file for a task | Using `docs/ARCHITECTURE.md` alone |

## 7. Positioning

| Alternative | What it does well | Where TrustBench is stronger |
|---|---|---|
| A tutorial notebook (for example the "ai-eval-system" notebook) | Teaches the EDD idea with a clear business-cost story | A real tool: persisted runs, a gate, CI, a server, tests. The cost story is a feature (R15). |
| Declarative CLI tools (Promptfoo style) | A simple YAML test file | Statistics with intervals, judge validation, a release gate, production loop |
| Benchmark harnesses (lm-evaluation-harness, Inspect AI) | Many public benchmarks | Evaluating your own app and agent, and deciding on a release |

We do not claim more benchmark coverage than those tools. We have 10 small built-in suites.

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| A stale cache hides a regression | A wrong "no change" verdict | Version label or git commit in the key. TrustBench does not cache a dirty tree without a label. Tests prove it. |
| A judge model is wrong or biased | Wrong scores | `validate-judge`, `judge-bias`, human labels, `human_review` flags |
| User code runs on the server | Remote code execution | Only the CLI and pytest enable app targets. A test walks the server modules. |
| Small datasets give weak verdicts | Engineers distrust the tool | The report shows intervals. The gate says "inconclusive". The docs explain how many cases we need. |
| We did not test against live services yet | Hidden integration bugs | `docs/ISSUES.md` lists it. We plan one live run before release. |

## 9. Release criteria for Phase 8

1. Milestones M1–M6 in `docs/TASKS.md` are done.
2. `pytest`, `ruff`, `trustbench selftest` are green.
3. `make edd-demo` runs end to end with no API keys.
4. A `/cso` security scan of the whole codebase is done and its findings are fixed or listed in `docs/ISSUES.md`.
5. Do not push to GitHub until the owner approves.
