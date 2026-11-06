# TrustBench — Known Issues and Limits

This file lists what is not done, not verified, or limited. We keep it honest.
Severity: **High** = can give a wrong result or a security risk. **Medium** = limits use. **Low** = small gap.
Status: Open, Planned (has a task in `docs/TASKS.md`), Accepted (a choice we made), Fixed.

## Not verified yet

| ID | Issue | Sev | Status | Notes |
|---|---|---|---|---|
| I-01 | No call to a live model provider was ever made | High | Open | All tests use the mock provider and `httpx.MockTransport`. Run one small real run and record it in `docs/PERFORMANCE.md` before release. |
| I-02 | Not run against a real PostgreSQL server | High | Open | Tests check Postgres as generated SQL only. Tests use SQLite. Migration 0005 uses `batch_alter_table`. Check it on Postgres. |
| I-03 | Not run against a real Redis server | Medium | Open | Tests use `tests/fake_redis.py` for the job queue. |
| I-04 | We never built the Docker images here | Medium | Open | The Docker daemon did not run on the dev machine. Starting it is the owner's decision. CI builds the image and checks its health (M5), but CI never ran. We ran the server outside Docker on SQLite. The health check returned 200. |
| I-05 | The dashboard has no tests | Low | Open | The typecheck and the production build pass. We ran them locally with pnpm 11 and Node 24. CI also runs them. |
| I-06 | Tests run distributed workers with one process and a simulated crash | Low | Accepted | |

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
| I-30 | We never ran the workflow and the Action on GitHub | High | Open | Tests cover everything that we can check locally. This includes the YAML structure, pinned SHAs, script injection, option names, the comment, and the poster against a simulated API. We also ran the install command in a fresh virtualenv. The first real run will teach us more. |
| I-31 | A baseline from `main` needs a database or a `baseline-target` in the same checkout | Medium | Accepted | `ci check --baseline-target` runs both targets in one job on the same cases. Comparing a pull request with the code of `main` needs a baseline stored between jobs (the Action caches answers, not named baselines). |
| I-32 | `requires-python` says 3.11 or newer, but we test only 3.12 | Low | Open | CI uses 3.12. |
| I-33 | A cached answer database holds model outputs. Anyone who can read the repository's Actions caches can read them. | Medium | Accepted | Do not put secrets in test data. Set the Action input `cache: "false"` to turn it off. |
| I-34 | `proposals export DATASET` uses the same text as the name of the proposals group and as the path of the base dataset | Low | Open | Use the file path of your dataset everywhere, as the demo does. Then the export includes the base cases. |
| I-35 | The example agent uses a scripted stand-in for the model, not a real model | Low | Accepted | By design: it is deterministic and needs no key. A live run against a real model is still I-01. |
| I-36 | We rendered the new dashboard pages (Variant history, Steps the app took) to HTML and type-checked them. Nobody looked at them in a browser | Low | Open | Run `trustbench demo`, `trustbench serve` and `pnpm dev`, then open /experiments and a case from an app-target run. |
| I-37 | Evaluation runs store tool-call arguments and results and the output of your app as they are, unredacted. TrustBench redacts production traces and can encrypt them. It does not do this for evaluation data. | Medium | Accepted | Do not put real customer data or secrets in test data or tool results. The dashboard and API show them to any key that can read the workspace. |
| I-38 | Evaluator plugins and the pytest plugin load from any installed package through entry points, also in the server process | Low | Accepted | Install only packages you trust, as with any Python environment. A plugin cannot replace a built-in evaluator. |
| I-39 | `docker-compose.yml` uses the database password `trustbench` when you do not set POSTGRES_PASSWORD | Low | Accepted | The port is bound to 127.0.0.1. Set a password in `.env` (see `.env.example`) before you share the machine. |
| I-40 | `.github/CODEOWNERS` exists, but GitHub enforces it only if "Require review from Code Owners" is on in the branch protection of main | Low | Open | A setting in the repository, to do after the first push. |
| I-41 | The writing check covers the rules that a program can check. It does not check the choice of words, `-ing` forms or the meaning. | Low | Accepted | A person must still read new text. |
| I-27 | The pytest plugin stores answers in `.trustbench/pytest.db` (SQLite). Two pytest processes at once (for example `pytest -n`) can lock it. | Low | Open | Use `--trustbench-db` with Postgres, or run evaluation tests in one process. |
| I-18 | A static price table. No spend history per workspace. No budget alerts. | Low | Open | TrustBench never counts an unknown price as zero. |
| I-26 | TrustBench does not cache judge answers. A judge-based test needs the judge's API key, even in pytest replay mode | Medium | Open | Use rule-based evaluators (`tool_call`, `trajectory`, `task_success`, `exact_match`) for free replay. Caching judge calls is a possible later task. |
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
| I-25 | A whole-codebase security scan (`/cso`) was done for Phase 8 | – | Fixed | 6 findings, all fixed (see "Security scan" below). Not a substitute for a professional audit. |

## Design notes that look like bugs but are not

- `trustbench run --model python:...` with a dirty git tree and no `@label` does not use the cache. This is on purpose. Add a label or commit.
- `tool_call` with `expected.tool_calls: []` means that the app must call no tool. It does not mean "nothing to check".
- A rate of 100% on 10 cases can still be "inconclusive" under a confidence-bound policy. The interval is wide. Add cases.
- The flat `src/trustbench/` root has about 70 modules. New code goes in packages. See `docs/ARCHITECTURE.md`.

## Fixed during Phase 8

| What | How we found it |
|---|---|
| The real `trustbench` command did not find Python targets in your project folder | A test that runs the real script (the earlier tests used `syspath_prepend`, which hid it) |
| The CI comment double-escaped characters (`&#95;` became `&&#35;95;`) | Reading the snapshot of the comment |
| A pytest plugin test was flaky because Python reused a stale `.pyc` of a file rewritten in the same second | A failure in a full run that passed alone |
| The first isolation test for the span recorder passed even with a shared global recorder | A deliberate break (mutation) survived. The test app now yields mid-call. |
| The default relevance threshold of 0.67 rejected a score of 2 of 3 | A test written for the boundary failed. The threshold is now `min_score: 2`. |

## Security scan (Phase 8, `/cso`)

Scope: the whole repository, with focus on app targets, the Action and workflow, the PR comment and poster, plugins and the new API routes.
Result: 0 critical, 1 high, 3 medium, 2 low. We fixed all of them in the same change. Each fix has a test that we saw fail on the old behaviour.

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | `certifi` 2026.7.22 had 2 advisories (CA bundle for TLS to providers and GitHub) | Pinned 2026.10.18 |
| 2 | Medium | `filelock` 4.2.0 had 17 advisories (dev tooling in the lockfile) | Pinned 4.2.1 |
| 3 | Medium | The Action put `GITHUB_TOKEN` in the environment of the step that runs your app and its dependencies | Only a separate step that runs TrustBench's own poster holds the token |
| 4 | Medium | The Action installed TrustBench without the pinned dependency versions | `pip install -c requirements.lock` |
| 5 | Low | The project folder was first on `sys.path`, so a stray stdlib-named file could shadow the real module | We append the folder last |
| 6 | Low | No CODEOWNERS for the workflow, Action, Dockerfile and lockfile | Added (needs branch protection, I-40) |

Checked and clean: secrets in git history and tracked env files, `eval`/`exec`/`shell=True`/`pickle`/unsafe YAML, SQL built from strings, disabled TLS verification,
unsafe HTML in the dashboard, `pnpm audit`, script injection and unpinned actions in the workflow, the escaping of the PR comment, the poster (repository name check,
bot-only edits, no token in messages), and that the server cannot run `python:` or `http:` targets.
We did not do three things. We did not run a live penetration test of a running server. We did not review the Docker image contents (the daemon was off). We did not check anything on GitHub itself. This scan is a first pass, not a professional audit.
