# TrustBench — Architecture

This is the map of the code. Read it first. Then open the file you need.
Last updated: Phase 8, milestone 6 (done).

## 1. One picture

```
 dataset (jsonl/json/csv)      trustbench.yaml            your app (python: / http:) or a model (openai:, ...)
        │                            │                                   │
        ▼                            ▼                                   ▼
  datasets.py ──────▶  cli.py ──▶ engine.py ───────────────▶ providers/  (one interface: Provider.generate)
  (versioned,          (run)      cache · resume · rate limit            │  returns text + tokens + tool_calls + spans
   validated)                     budget · retries                       ▼
                                      │                        providers/recorder.py (spans from your app)
                                      ▼
                         evaluators.py registry ──▶ eval_*.py, agents/*   (each returns pass/fail + reason)
                                      │
                                      ▼
                  db.py tables: responses and scores are separate (rescore without new calls)
                                      │
        ┌─────────────────────────────┼──────────────────────────────┐
        ▼                             ▼                              ▼
  rundata.py ─▶ scorecard · slices · comparison · gate · failures    api.py / api_data.py ─▶ dashboard/
  (plain objects,  (statistics: stats.py)                            (server, workspaces)
   no DB inside)                                                          ▲
                                                                          │
  production: sdk.py ─▶ traces.py ─▶ online.py · drift.py · monitor.py ───┘
                                      └─▶ production_tests.py ─▶ proposals.py ─▶ new dataset version
```

## 2. Layers and rules

| Layer | Modules | Rule |
|---|---|---|
| Interface | `cli*.py`, `api*.py`, `dashboard/` | Parse input, call the layers below, format output. No statistics here. |
| Workflow | `edd/`, `engine.py`, `gate.py`, `policy.py`, `baselines.py`, `proposals.py` | Orchestrate. Decide. |
| Analysis | `rundata.py`, `scorecard.py`, `comparison.py`, `slices.py`, `failures.py`, `calibration.py`, `robustness.py`, `frontier.py`, `stats.py` | Pure functions on `rundata` objects. No database access. |
| Scoring | `evaluators.py`, `eval_*.py`, `agents/`, `judge*.py`, `claims.py`, `verify.py` | One evaluator in one file. Returns an `Outcome`. |
| Targets | `providers/` | Hide every vendor and every app behind `Provider`. |
| Storage | `db.py`, `migrate.py`, `migrations/`, `bulk.py` | The only place that knows the table layout. |
| Safety | `redact.py`, `crypto.py`, `dataprotect.py`, `tenancy.py`, `web_security.py`, `audit.py`, `ratelimit.py` | Applied at the edges. |

Dependency direction: Interface → Workflow → Analysis/Scoring/Targets → Storage. A lower layer never imports a higher one.

## 3. Module map

Packages first. Then the flat modules in `src/trustbench/`, grouped by job.

### Packages
| Path | Job |
|---|---|
| `providers/` | `base.py` (interface, prices), `http.py` (Anthropic, OpenAI-compatible), `mock.py`, `app.py` (python:/http: targets), `recorder.py` (tool calls and spans) |
| `agents/` | Evaluators for agents. Files: `eval_tool_call.py`, `eval_trajectory.py`, `eval_task_success.py`, `eval_rag_judged.py`. `matchers.py` holds expected values. `schema.py` holds load-time checks. |
| `ci/` | Pull request checks: `comment.py` (Markdown comment, escaping, size cap), `github.py` (post or update), `check.py` (run, gate, comment). The Action `action.yml` and `.github/workflows/ci.yml` are in the repo root. |
| `edd/` | EDD workflow. `config.py` reads trustbench.yaml. `plugins.py` loads evaluator plugins. `pytest_plugin.py` runs evaluations in pytest. `experiments.py` holds variants, hypotheses and verdicts. `costmodel.py` gives dollars per year with an interval. `runner.py` holds the run steps that the CLI, CI and pytest share. |
| `selftest/` | 33 checks that prove TrustBench measures correctly: `checks_stats.py`, `checks_eval.py`, `checks_agents.py`, `checks_system.py` |
| `suites/` | Ten built-in datasets (`smoke`, `factual`, `grounded`, `hallucination`, `injection`, `safety`, `abstention`, `calibration`, `structured_output`, `instruction_following`) |
| `migrations/` | Alembic versions 0001–0006 |
| `dashboard/` (repo root) | Next.js app. Reads the API. |
| `examples/support_agent/` (repo root) | The flagship example: a tool-using agent in four versions, its dataset and policy, and `demo.py` (the EDD story). Run with `make edd-demo`. |

### Flat modules by job
| Job | Modules |
|---|---|
| Run | `engine.py`, `datasets.py`, `config.py` (environment), `rundata.py`, `overview.py`, `report.py`, `report_export.py` |
| Score (rules) | `evaluators.py` (registry), `eval_reference.py`, `eval_numeric.py`, `eval_schema.py`, `eval_calibration.py`, `eval_injection.py`, `eval_grounded.py`, `eval_judge.py` |
| Score (judge) | `judge.py`, `judge_validation.py`, `judge_bias.py`, `agreement.py`, `pairwise.py`, `claims.py`, `verify.py`, `hallucination.py`, `annotations.py` |
| Statistics | `stats.py`, `scorecard.py`, `comparison.py`, `slices.py`, `calibration.py`, `operations.py`, `frontier.py` |
| Decide | `policy.py`, `gate.py`, `baselines.py`, `edd/costmodel.py`, `edd/experiments.py` |
| Diagnose | `failures.py`, `clustering.py`, `rag.py`, `priority.py`, `failure_report.py` |
| Harden | `transforms.py`, `paraphrase.py`, `robustness.py`, `adversary.py`, `quality.py` |
| Close the loop | `proposals.py`, `production_tests.py` |
| Production | `sdk.py`, `traces.py`, `online.py`, `drift.py`, `monitor.py`, `jobs.py`, `handlers.py`, `dataprotect.py` |
| Server and access | `api.py`, `api_data.py`, `tenancy.py`, `web_security.py`, `ratelimit.py`, `audit.py`, `crypto.py`, `redact.py` |
| Storage | `db.py`, `migrate.py`, `bulk.py` |
| CLI | `cli.py`, `cli_stats.py`, `cli_server.py`, `cli_db.py`, `cli_selftest.py`, `cli_edd.py` (init, plugins, cost, experiment), `cli_ci.py` (ci check, comment, post), `cli_common.py`, `demo.py` |

The flat root is large (about 70 modules). The rule for new code: **put it in a package, not in the root.**
Moving old modules into packages is a possible later cleanup. It needs its own commit and a full test run.

## 4. The run, step by step

1. `cli.run` loads the dataset (`datasets.load_cases`). It checks it (`quality.check`) and stores a version (`import_dataset`). A stored version never changes.
2. `engine.run_eval` makes one execution for each case × model × repetition. It builds a `Provider` for each provider name (`providers.get_provider`).
3. For each execution it looks in the answer cache. The key is a hash of provider, model, prompt, system, temperature, max tokens, repetition, and a **salt**.
   The salt is empty for models. For app targets it is the version label or the git commit (`providers/app.py: code_fingerprint`). `None` means: do not cache.
4. On a miss it calls `provider.generate(GenRequest)` with a timeout and retries. A `PythonAppProvider` runs your function inside a `recording()` context.
5. It runs the evaluators (`evaluators.evaluate`). The context (`Ctx`) holds the output, the expected values, the judge, `tool_calls` and `spans`.
6. A writer saves responses, scores and cache rows in batches. Responses and scores are in different tables.
7. Analysis reads the run with `rundata.load_run` and works on plain objects.

## 5. Extension points

| To add | Do this | Docs |
|---|---|---|
| A model provider | Subclass `Provider` in `providers/`. Register it in `get_provider`. | `providers/base.py` |
| Your own app as a target | Write a function. Run `--model python:pkg.mod:fn@v1`. Use `span()` and `tool_call()` to record steps. | `providers/app.py` |
| An evaluator in this repo | New file in `agents/` or a root `eval_*.py`. Use `@register`. Add a gold-set item and a test that breaks it. | `evaluators.py` |
| An evaluator from another package | Entry point group `trustbench.evaluators`. | `edd/plugins.py` |
| A selftest check | `@check(...)` in `selftest/`. Add a sabotage test in `tests/test_selftest.py`. | `selftest/__init__.py` |
| A database column | Change `db.py`. Add a migration. `test_migrations.py` fails until you do. | `migrate.py` |

## 6. Invariants (enforced by tests)

1. A dataset version never changes. A change makes a new version.
2. A run stores a snapshot of its configuration when it starts.
3. Each score records its evaluator name and rubric version.
4. Raw outputs and scores are in different tables. We can rescore with no model call.
5. Each retry is a new row. A retry never replaces a failure.
6. A run with skipped cases is "incomplete". TrustBench never counts an unknown price as zero.
7. Each release decision records its policy and evidence.
8. TrustBench reports a broken evaluator and leaves it out. It never counts it as a model failure.
9. The cache key of a model answer does not change when we add features (`test_cache_keys_of_model_answers_are_unchanged`).
10. The server never runs `python:` or `http:` targets (`test_no_server_module_turns_app_targets_on`).

## 7. Security model

- CI: the Action and the workflow pin every third-party action to a commit SHA, run with `contents: read`, and pass inputs through `env`.
  The comment escapes everything that came from a model or from test data (HTML, links, images, mentions, tables, code, math, bare URLs).
  The poster edits only a bot comment that starts with the marker, validates the repository name, and never prints the token.
  Use the Action in a `pull_request` workflow, never `pull_request_target`.

- API keys have roles (read, write, admin) and can expire. Workspaces isolate data.
- TrustBench redacts production content before it stores it. You can encrypt stored content (Fernet).
- App targets run your code or call your URL. Only the CLI and the pytest plugin turn them on (`providers.allow_app_targets`).
  The API, the worker and online evaluation never do. A server-side judge cannot be a `python:` target.
- The server has no TLS of its own. Use a proxy. See `docs/ISSUES.md` for known limits.

## 8. Platform decisions

These came from the owner at the start of the project and still hold.

| Decision | Detail |
|---|---|
| Database | PostgreSQL. Users start it with `docker compose up -d postgres`. |
| Queue | Redis for distributed workers (`jobs.py`). Tests use `tests/fake_redis.py`. |
| Dashboard | Next.js as a separate server. It talks to the API. |
| Tests | In-memory SQLite and the mock provider. They need no API key and no running service. |
| Access | Multi-tenant keys with roles. You can encrypt stored content. |
| Providers | Plain HTTP with httpx. No vendor SDKs. |
| Git | Write commit messages like a human. Do not add AI trailers. |

## 9. Conventions

- One concern in each file. A short docstring at the top says what it does and how to use it.
- Follow the writing rules in docs and comments. Use up to 20 words in an instruction. Use up to 25 words in other sentences. Use the active voice. Give one instruction in each sentence. Use one word for one meaning. Use vertical lists for complex text. Run `make writing` to check these rules.
- Comments explain *why*, only where the reason is not clear from the code.
- Every behaviour has a test. Every new check has a test that breaks the code it watches.
- No push to the remote until the owner approves.
