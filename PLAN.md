# TrustBench — Build Plan

Language rule: this plan and all project text use ASD-STE100 style.
- Write short sentences. Procedures: 20 words maximum. Descriptions: 25 words maximum.
- Use the active voice. Write one instruction in each sentence.
- Use one word for one meaning. Do not use synonyms for the same thing.
- Use vertical lists for complex text.

Scope rule: if a part does not help the user get a correct evaluation result, we do not build it.

## 1. Goal

The user installs TrustBench. The user sets one API key. TrustBench does the rest.

```bash
pip install trustbench
export ANTHROPIC_API_KEY=...
trustbench run
```

`trustbench run` does these steps:
1. It finds the API keys in the environment.
2. It selects the built-in benchmark suites.
3. It runs the models and stores all raw outputs.
4. It scores the outputs.
5. It calculates the statistics.
6. It opens the dashboard in the browser.

## 2. Out of scope

- CI/CD integration (GitHub Actions).
- Research layer (section 17 of the source document).
- Long documentation. We write one short README only.
- Multimodal evaluation.

## 3. Architecture

Decisions from the owner:
- Database: Postgres. Users start it with `docker compose up -d`.
- Redis and a task queue: added in the phase that needs them (distributed workers, Phase 5).
- Multi-tenant access control and encryption of stored secrets: added in Phase 5.
- Dashboard: Next.js as a separate server (Phase 6).
- No local services on the owner's Mac. Tests use in-memory SQLite and a mock provider. No API keys are needed.
- Git: human commit messages. No AI trailers.

One Python package. One database. Layout (files that exist today):

```text
src/trustbench/
  cli.py  config.py  db.py  datasets.py  evaluators.py  engine.py  report.py
  providers/   base, http (Anthropic, OpenAI-compatible, Gemini, local), mock
  suites/      built-in benchmark files
tests/
```

Data model: DatasetVersion, TestCase, ModelConfiguration, EvaluationRun, TestExecution, ModelResponse, EvaluationResult, HumanAnnotation. More tables come with later phases.

Phase 2 modules: eval_* (evaluators), judge, pairwise, claims, verify, hallucination, quality, annotations, agreement, judge_validation, judge_bias, stats (Wilson interval).

Phase 3 modules: stats, rundata, scorecard, operations, comparison, slices, calibration, policy, gate, baselines, summary, cli_stats. Tables added: baselines, release_decisions.

Phase 4 modules: transforms, paraphrase, robustness, eval_injection, adversary, proposals, failures, clustering, rag, priority, failure_report, frontier. Table added: proposed_cases. Suite added: injection.

Phase 5 modules: tenancy, redact, crypto, dataprotect, audit, traces, api, sdk, jobs, handlers, online, drift, production_tests, monitor, cli_server. Tables added: workspaces, api_keys, audit_log, production_traces, trace_spans, trace_evaluations, jobs.

Open items before the first release:
- Tables are created with `create_all`. Add Alembic migrations (a column change needs one).
- Runs, datasets, baselines and proposals from the CLI are not scoped by workspace. Scope them when Phase 6 serves them over the API.
- The server has no TLS, rate limit or key expiry.
- Postgres, a real Redis server and live model APIs are not tested yet (no services on the development machine).
- A Dockerfile and compose services for the API and the worker.

Older note: tables are created with `create_all`. A change to a table needs a migration tool (Alembic). We add it before the first release.

Invariants (we enforce them in code and tests):
1. A dataset version never changes. A change makes a new version.
2. The run stores a snapshot of its configuration when it starts.
3. Each score records its evaluator name and rubric version.
4. Raw outputs and scores are in different tables.
5. Each retry is a new row. A retry does not replace a failure.
6. A run that has skipped cases shows the status "incomplete".
7. Each release decision records its policy and evidence (Phase 3).

## 4. Phases

Each phase gives a working system. Each phase has tests before we continue.

### Phase 1 — Core engine (DONE)
- Provider adapter interface. Anthropic and OpenAI adapters first. Then Gemini and OpenAI-compatible.
- Record tokens, latency, cost estimate, errors, model ID, inference settings.
- JSONL dataset loader with schema validation and content hash versions.
- Exact-match and rule evaluators.
- Generation mode and re-scoring mode.
- CLI `trustbench run` with automatic key detection.
- Exit test: re-scoring uses stored outputs. It sends zero model calls.

### Phase 2 — Scoring quality (DONE)
- Schema, programmatic, reference, LLM-judge and pairwise evaluators.
- Claim-level hallucination pipeline: claims -> evidence -> Supported / Contradicted / Insufficient / Not applicable.
- Metrics: unsupported claim rate, contradiction rate, evidence coverage, unassessable fraction.
- Dataset quality checks: duplicates, near-duplicates, missing references, conflicts, imbalance, PII.
- Built-in suites: factual QA, grounded QA, hallucination traps, instruction following, structured output, abstention, safety, calibration.
- Human annotation and judge validation (agreement, kappa, position and verbosity bias).

### Phase 3 — Statistics and release gate (DONE; CLI and API commands for CI use are in, CI integration itself is out of scope)
- Wilson CI, bootstrap CI, McNemar, paired bootstrap, permutation test.
- Practical threshold: Improvement / Regression / Inconclusive.
- Slice analysis with sample sizes and Benjamini-Hochberg control.
- Calibration: Brier score, ECE, AUROC, risk-coverage curve.
- Release policy YAML. Status: Pass, Fail, Inconclusive, Incomplete.
- CLI `compare` and `gate`. Non-zero exit code on Fail.

### Phase 4 — Robustness and failures (DONE, except distributed workers and Redis: moved to Phase 5)
- Metamorphic transforms with invariants: paraphrase, format, reorder, noise, evidence position.
- Robustness metrics: transformation failure rate, degradation, consistency, worst slice.
- Prompt-injection suite for RAG and tool calls.
- Adversarial generator. Output goes to a "proposed" state. A human approves it.
- Failure taxonomy, embedding clusters, RAG stage diagnosis, priority score.
- Cost/latency: P50/P95/P99, cost per successful task, Pareto frontier.

### Phase 5 — Production loop (DONE)
- Small SDK: `trustbench.trace(...)`. It sends traces to the local server.
- Redaction and retention settings.
- Online evaluation by sampling. Drift detection (PSI, KS test).
- Trace -> proposed regression test -> human approval -> suite.

### Phase 6 — Dashboard
Pages: Overview, Experiments, Benchmarks, Models, Evaluators, Reliability Lab, Failure Explorer, Regression Center, Production Monitor, Settings.
- Each number links to the test cases that make it.
- Each score shows its CI and sample size.
- Dark observability style.

### Phase 7 — Validation suite (section 21)
- Synthetic data with known differences. Check CI coverage by simulation.
- Inject known regressions. Check that the gate finds them.
- Inject duplicates and bad labels. Check that the quality checks find them.
- Mock provider for all tests. Tests do not need API keys.

## 5. How you learn from this build

- After each phase I give a short explanation: what we built, why, and the main concept (for example, why McNemar and not a t-test).
- The code has short comments only where the reason is not clear.
