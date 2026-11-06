# TrustBench — Tasks

Phase 8: Eval-Driven Development (EDD). Plan source: the approved plan, summarized in `docs/PRD.md`.
Rules: commit on the local machine after each milestone. **Do not push** until the owner says so.
Each milestone ends with `pytest`, `ruff check src tests`, and `trustbench selftest` green.

Legend: [x] done · [~] in progress · [ ] to do

## Completed: Phases 1–7 (the platform)

| Phase | What was built |
|---|---|
| 1. Core engine | Provider interface (Anthropic, OpenAI, Gemini, OpenAI-compatible, local, mock). Tokens, latency, cost, errors. JSONL datasets with content-hash versions. Exact-match and rule evaluators. Generate and re-score modes. `trustbench run` with key detection. Re-scoring sends zero model calls. |
| 2. Scoring quality | Schema, programmatic, reference, LLM-judge and pairwise evaluators. Claim-level hallucination pipeline (supported, contradicted, insufficient, not applicable). Dataset quality checks. Ten built-in suites. Human annotation and judge validation (agreement, kappa, position and length bias). |
| 3. Statistics and gate | Wilson and bootstrap intervals, McNemar, paired bootstrap, permutation test, practical thresholds. Slice analysis with Benjamini-Hochberg control. Calibration (Brier, ECE, AUROC, risk-coverage). Release policy YAML. Gate: pass, fail, inconclusive, incomplete. `compare` and `gate` commands with exit codes. |
| 4. Robustness and failures | Metamorphic transforms. Robustness metrics (break rate, degradation, consistency, weakest transform). Prompt-injection suite. Adversarial proposals, always approved by a person. Failure taxonomy, clusters, RAG stage diagnosis, priority score. P50/P95/P99 latency, cost per success, Pareto frontier. |
| 5. Production loop | SDK (`trustbench.sdk`). Redaction and retention. Online evaluation by sampling. Drift (PSI, KS test). Trace → proposed test → approval → dataset version. Workers and a job queue. Workspaces and keys. |
| 6. Dashboard | Overview, runs with detail, compare and gate, benchmarks with proposal review, models, production monitor, settings. Every number links to its cases. Every score shows its interval and size. Not built: an Evaluators page and a Reports page (see I-15). |
| 7. Validation | `trustbench selftest` with simulations of known truth. Each check is tested by breaking the code it watches. Migrations, key expiry, rate limits, run report export, Docker files. |

Later hardening: batched writes, answer cache, resume, rate limit, composite indexes, API edge hardening (see `docs/PERFORMANCE.md`).

## M1 — App targets  [x]  (commit `e69fa1b`)
- [x] `providers/app.py`: `PythonAppProvider`, `HttpAppProvider`, `@label` versions, git fingerprint
- [x] `providers/recorder.py`: `span()`, `tool_call()`, isolated per case
- [x] `GenRequest.input`, `GenResponse.tool_calls/spans`, `Provider.cache_salt`
- [x] Engine: salt in the cache key, store and replay tool calls and spans
- [x] Migration 0005 (`model_responses`, `response_cache`)
- [x] Server never runs app targets (`allow_app_targets` only in CLI)
- [x] 45 tests, mutation-checked

## M2 — Agent and RAG evaluators  [x]  (commit `1882fea`)
- [x] `agents/matchers.py`, `eval_tool_call.py`, `eval_trajectory.py`, `eval_task_success.py`, `eval_rag_judged.py`
- [x] Conversations as input (`input.messages`). Load-time validation (`agents/schema.py`).
- [x] Failure labels: wrong_tool, bad_args, looped, gave_up, task_not_completed, irrelevant_answer, poor_retrieval
- [x] Three selftest checks with sabotage tests (31 checks in total)

## M3 — Config, pytest plugin, plugins  [x]
- [x] `edd/config.py`: `trustbench.yaml` model, loader, template. `trustbench init`.
- [x] `trustbench run` reads `trustbench.yaml` (`--config`, `--target`). Flags win over the file.
- [x] `edd/plugins.py`: entry point group `trustbench.evaluators`. A broken plugin is skipped. A plugin cannot replace a built-in. `trustbench plugins`.
- [x] `edd/pytest_plugin.py`: `trustbench` fixture, replay by default, `--trustbench-live`, `--trustbench-budget`, gate verdict. `pytest11` entry point.
- [x] Docs: `PRD.md`, `ARCHITECTURE.md`, `ISSUES.md`, `TASKS.md`, `DATA.md`, root `MEMORY.md`. `PLAN.md` merged here and removed. `LEARNING.md` removed.
- [x] 66 new tests (config, CLI, plugins, pytester-based plugin tests), mutation-checked
- [x] Commit (local only)

## M4 — Experiments, cost model, prompt versions  [x]
- [x] `edd/experiments.py`: `experiment record / list / compare`. Verdict: supported, refuted, mixed (gain hides a worse slice), no_effect, cannot_tell.
- [x] `edd/costmodel.py` + `trustbench cost`: confusion-cost matrix, volume, $/year with an interval, manual and perfect baselines. Reproduces the $380,000 / $100,000 reference example.
- [x] Policy: `cost_model` block and `annual_cost_max_usd`. Gate rule `annual_cost`: fail over the limit; inconclusive when the interval straddles it or the cost is unknown.
- [x] Prompts: `--system-prompt-file` / `run.system_prompt_file`. The text is stored in the run snapshot. `experiment compare` shows the diff. Python and HTTP targets can receive `system`.
- [x] Migration 0006 (`experiments`)
- [x] Two selftest checks (hand calculation, interval coverage) with sabotage tests. 33 checks in total.
- [x] 51 new tests, mutation-checked
- [x] Commit (local only)

## M5 — CI/CD  [x]
- [x] `.github/workflows/ci.yml`: ruff, pytest, selftest; Docker build and health check; dashboard typecheck and build. Actions pinned to commit SHAs. Least permission.
- [x] `action.yml` (reusable composite Action): `trustbench ci check`, cached answers, report artifact. Inputs go through `env`, never into the script text.
- [x] `trustbench ci check | comment | post`; `ci/comment.py` (comment with escaping of untrusted text, size cap), `ci/github.py` (update in place, only bot comments with the marker), `ci/check.py`
- [x] `edd/runner.py`: shared run steps for `run`, `ci check` and the pytest plugin
- [x] Golden snapshot test of the comment. Tests that lint the workflow and Action (pinned SHAs, no script injection, options exist).
- [x] Verified locally: the exact CI install command in a fresh virtualenv, the server health check on SQLite, the dashboard typecheck and build
- [ ] Not verified: the workflow and the Action on GitHub (they were never run there), the Docker job (the Docker daemon was not running here)
- [x] Commit (local only)

## M6 — Flagship example and docs  [~]
- [x] `examples/support_agent/`: tools, a scripted stand-in for the model, v0–v3, 48 cases (single-turn, multi-turn, safety, edge), `policy.yaml` with a cost model
- [x] `make edd-demo` (and a CI job): v0 fails → failures → v1 better but blocked → v2 passes → production trace → promote → approve → export → v3. Runs with no API key.
- [x] Tests keep the story true (scores, labels, gate verdicts, the committed dataset, the whole demo)
- [x] `docs/EDD.md`; README section "Eval-Driven Development in 5 minutes"; example README
- [x] Found and fixed: the current folder was not importable by the `trustbench` script (python targets in your project were not found)
- [ ] Dashboard: Experiments page and a tool-call / trajectory view on the run detail page
- [ ] Commit (local only)

## Final  [ ]
- [ ] Full `/cso` scan of the whole codebase. Fix findings. List what remains in `docs/ISSUES.md`.
- [ ] Update `docs/*`, `README.md`, `MEMORY.md`
- [ ] One live run against a real provider (I-01), recorded in `docs/PERFORMANCE.md`
- [ ] Ask the owner before any push

## Backlog (not scheduled)
- Move old flat modules into packages (needs its own commit and a full test run)
- Red-team generator and OWASP mapping (I-13)
- Dev / test / holdout splits and a contamination check (I-17)
- Labeling UI (I-14). Evaluators and Reports pages (I-15).
- Baseline names per workspace (I-22)
