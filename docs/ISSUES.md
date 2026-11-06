# TrustBench — Known Issues and Limits

This file lists what is not done, not verified, or limited. We keep it honest.
Severity: **High** = can give a wrong result or a security risk. **Medium** = limits use. **Low** = small gap.
Status: Open, Planned (has a task in `docs/TASKS.md`), Accepted (a choice we made), Fixed.

## Not verified yet

| ID | Issue | Sev | Status | Notes |
|---|---|---|---|---|
| I-01 | No call to a live model provider was ever made | High | Open | All tests use the mock provider and `httpx.MockTransport`. Run one small real run and record it in `docs/PERFORMANCE.md` before release. |
| I-02 | Not run against a real PostgreSQL server | High | Open | Postgres is checked as generated SQL only. Tests use SQLite. Migration 0005 uses `batch_alter_table`. Check it on Postgres. |
| I-03 | Not run against a real Redis server | Medium | Open | The job queue is tested with `tests/fake_redis.py`. |
| I-04 | Docker images were never built here | Medium | Open | The Docker daemon was not running on the dev machine, and starting it is the owner's call. CI builds the image and checks its health (M5), but CI was never run. The server was checked outside Docker on SQLite (health 200). |
| I-05 | The dashboard has no tests | Low | Open | The typecheck and the production build pass (checked locally with pnpm 11 and Node 24; also in CI). |
| I-06 | Distributed workers are tested with one process and a simulated crash | Low | Accepted | |

## Product gaps

| ID | Issue | Sev | Status | Notes |
|---|---|---|---|---|
| I-10 | No judge-based argument matcher for tool calls | Low | Open | Matchers: exact, approx, regex, in, contains, type, schema, any. For fuzzy arguments use `llm_judge` on the output. |
| I-11 | `task_success` checks are code only | Low | Accepted | For a rubric on open-ended success use `llm_judge`. |
| I-12 | Few public benchmarks (10 small suites) | Medium | Accepted | Not part of the EDD loop. |
| I-13 | No red-team attack generator, no OWASP LLM mapping | Medium | Open | `injection` and `safety` suites are static. Out of scope for Phase 8. |
| I-14 | No labeling interface in the dashboard | Low | Open | Human labels use a CSV round trip. |
| I-15 | The dashboard has no Evaluators page (judge validation) and no Reports page | Low | Open | The report export exists as a file and an API route. |
| I-16 | RAG checks read documents from a `retrieval` span. A retriever that records nothing needs `input.context`. | Low | Accepted | The error message says what to do. |
| I-17 | Datasets are not split into dev, test and holdout. No contamination check. | Medium | Open | |
| I-30 | The workflow and the Action were never run on GitHub | High | Open | Everything checkable locally is tested (YAML structure, pinned SHAs, no script injection, option names, the comment, the poster against a simulated API, the install command in a fresh virtualenv). The first real run will teach us more. |
| I-31 | A baseline from `main` needs a database or a `baseline-target` in the same checkout | Medium | Accepted | `ci check --baseline-target` runs both targets in one job on the same cases. Comparing a pull request with the code of `main` needs a baseline stored between jobs (the Action caches answers, not named baselines). |
| I-32 | Python 3.11 is declared (`requires-python >=3.11`) but only 3.12 is tested | Low | Open | CI uses 3.12. |
| I-33 | A cached answer database holds model outputs. Anyone who can read the repository's Actions caches can read them. | Medium | Accepted | Do not put secrets in test data. Set the Action input `cache: "false"` to turn it off. |
| I-27 | The pytest plugin stores answers in `.trustbench/pytest.db` (SQLite). Two pytest processes at once (for example `pytest -n`) can lock it. | Low | Open | Use `--trustbench-db` with Postgres, or run evaluation tests in one process. |
| I-18 | A static price table. No spend history per workspace. No budget alerts. | Low | Open | An unknown price is never counted as zero. |
| I-26 | Judge answers are not cached, so a judge-based test needs the judge's API key even in pytest replay mode | Medium | Open | Use rule-based evaluators (`tool_call`, `trajectory`, `task_success`, `exact_match`) for free replay. Caching judge calls is a possible later task. |
| I-19 | Cost for HTTP and Python targets is unknown unless the app reports tokens | Medium | Accepted | The cost model reports `incomplete` and the gate says `inconclusive`. Set `processing_cost_per_item` to give it a value. |
| I-28 | The cost interval is the box of two 95% intervals, so it covers at least 90% jointly (the selftest measures about 99%). It is wider than needed. | Low | Accepted | Conservative on purpose. |
| I-29 | The cost model needs a `positive` label on each case and a real-world `positive_rate` from you. A wrong rate gives a wrong dollar figure. | Medium | Accepted | The report shows the error rates with their intervals, so you can check the inputs. |

## Security and operations

| ID | Issue | Sev | Status | Notes |
|---|---|---|---|---|
| I-20 | The server has no TLS | Medium | Accepted | Use a reverse proxy. |
| I-21 | Rate limits are per process | Medium | Accepted | Several server processes each keep their own count. |
| I-22 | Baseline names are global across workspaces | Medium | Open | One workspace can read or replace another's baseline name. |
| I-23 | Redaction is pattern based | Medium | Accepted | It can miss personal data that has no clear pattern. |
| I-24 | A `python:` target runs arbitrary code with the user's rights | Medium | Accepted | By design. Only the CLI and pytest enable it. Never run a dataset or config from an untrusted source with `python:` targets. |
| I-25 | A whole-codebase security scan (`/cso`) has not been done for Phase 8 | High | Planned | Last step of Phase 8. |

## Design notes that look like bugs but are not

- `trustbench run --model python:...` with a dirty git tree and no `@label` does not use the cache. This is on purpose. Add a label or commit.
- `tool_call` with `expected.tool_calls: []` means "no tool may be called". It is not "nothing to check".
- A rate of 100% on 10 cases can still be "inconclusive" under a confidence-bound policy. The interval is wide. Add cases.
- The flat `src/trustbench/` root has about 70 modules. New code goes in packages. See `docs/ARCHITECTURE.md`.

## Fixed during Phase 8

| What | How it was found |
|---|---|
| The first isolation test for the span recorder passed even with a shared global recorder | A deliberate break (mutation) survived. The test app now yields mid-call. |
| The default relevance threshold of 0.67 rejected a score of 2 of 3 | A test written for the boundary failed. The threshold is now `min_score: 2`. |
