# MEMORY.md — TrustBench project memory

Read this first in every session. It holds what to remember about the project.
Long-term memory changes rarely. Short-term memory is updated at the end of each work session.
Related docs: `docs/PRD.md` (what and why), `docs/ARCHITECTURE.md` (where), `docs/DATA.md` (data), `docs/TASKS.md` (work), `docs/ISSUES.md` (limits).

---

# Long-term memory

## What this project is
TrustBench evaluates LLMs and LLM apps. A user sets an API key (or points at their own function or service) and gets pass rates with intervals, a release gate, failure analysis and a production loop.
It ships to engineers for their own workflows. It is also the owner's showcase project for a resume and interviews. Quality and clarity matter more than feature count.

## Stack
Python 3.12, FastAPI, SQLAlchemy and Alembic (Postgres in production, SQLite in tests), Pydantic, Typer and Rich, httpx (no vendor SDKs), Next.js dashboard (pnpm), pytest, ruff.
Statistics are hand-written in `stats.py` (no numpy or scipy).

## Commands
| Do | Run |
|---|---|
| All tests | `python -m pytest -q` (about 30 s) |
| Lint | `ruff check src tests` |
| Self-test | `trustbench selftest` (`--quick` is faster) |
| Try with no key | `trustbench run --model mock:smoke` |
| Test your own function | `trustbench run --model python:pkg.mod:fn@v1` |

## Rules of the project (the owner's wishes)
1. **Never push to GitHub** (or open a PR) unless the owner says so. Commit locally only.
2. Commit messages are written like a human. No AI trailers (`docs/ARCHITECTURE.md` section 8).
3. Keep the code clean and easy to navigate. New code goes in a package (`agents/`, `edd/`, `providers/`, ...), not the flat root. One concern in each file. A short docstring at the top.
4. Docs and comments use short sentences (ASD-STE100 style). One idea in each sentence.
5. Every new behaviour has tests. Every new selftest check has a test that breaks the code it watches. After writing a test, break the code on purpose (mutation) and see it fail.
6. Keep `docs/` current: update `TASKS.md` at each milestone, `ISSUES.md` when a limit is found, `DATA.md` when the data changes, `ARCHITECTURE.md` when a module is added.
7. End of Phase 8: run `/cso` over the whole codebase. Fix the findings.
8. Do not add interview-prep or learning-guide files (`LEARNING.md` was removed on the owner's request). The owner will ask for them later.
9. Be honest about what was not verified (see `docs/ISSUES.md`, I-01 to I-05).

## Design decisions (and why)
| Decision | Why |
|---|---|
| App targets are `Provider` subclasses (`providers/app.py`) | The engine, cache, resume, rate limit and budget work unchanged. A small diff. |
| Cache key has a salt: version label or git commit. Dirty tree without a label is not cached. | A stale answer would silently corrupt an experiment comparison. |
| `python:` and `http:` targets are off unless the CLI or pytest turns them on | A server must never run user code. A test walks the server modules. |
| The pytest plugin replays from the cache by default | A normal `pytest` run must be free and fast. Live calls need `--trustbench-live`. |
| A plugin cannot replace a built-in evaluator | A third-party package must not change how existing scores work. |
| An evaluator error is reported and left out, never counted as a model failure | A broken checker says nothing about the model. |
| Unknown cost is `incomplete`, never zero | Zero would make an unpriced model look free. |
| Tool-call matching uses a search, not a greedy pass | A loose expectation listed first must not take the call that a strict one needs. |

## Invariants
See `docs/ARCHITECTURE.md` section 6. Short form: dataset versions never change; a run stores its config; responses and scores are in separate tables; a retry is a new row; incomplete runs say so.

## Gotchas
- `Evaluator` modules import at the bottom of `evaluators.py` (they need `register`). Keep that order.
- `tests/conftest.py` turns app targets off after every test. A test that needs them calls `allow_app_targets()`.
- `trustbench run` for a sync Python function: the function runs inside the event loop. Keep app functions fast or make them `async`.
- `sentence_claims` ignores sentences under 3 words. Short test outputs skip claim checks.
- The shell's working directory can drift into `src/`. Use absolute paths.

---

# Short-term memory (update each session)

## Current state — Phase 8 (EDD), 2026-11-06
- Branch `main`, local only. Nothing pushed. Never push without the owner's word.
- Done and committed: M1 app targets, M2 agent evaluators, M3 config + pytest plugin + plugins, M4 experiments + cost model + prompt files, M5 CI (workflow, Action, `ci` commands), M6 example + docs (see `git log`). Dashboard pages for experiments and tool calls are the last M6 item.
- Tests: 1057 pass. Ruff clean. Selftest 33 of 33. The workflow and Action were never run on GitHub (I-30). Docker daemon was off, so the image build is unverified (I-04). The plugin's `pytest11` entry point is registered in the venv (`pip install -e .`).
- `PLAN.md` and `docs/LEARNING.md` were removed. The docs set is in `docs/`.

## Next steps
1. M6 left: dashboard Experiments page and a tool-call / trajectory view.
2. `/cso` scan of the whole codebase. Fix findings.
3. Ask the owner about pushing. Also ask whether to start Docker Desktop so the image and Postgres migrations can be verified (I-02, I-04).

## Open questions for the owner
- When to push, and to which branch or remote.
- Whether to move old flat modules into packages (a bigger cleanup, risky, needs its own commit).

## Session log
- 2026-11-06: Built the flagship example (`make edd-demo`), `docs/EDD.md`, README section. Found that the current folder was not importable by the real `trustbench` script and fixed it. Built M5 (CI check, comment with escaping, poster, workflow, Action; found and fixed a double-escaping bug via the snapshot and a flaky stale-.pyc test). Built M4 (experiments, cost model reproducing the $380k/$100k example, prompt diff). Built M3. Removed PLAN.md (merged into docs) and LEARNING.md. Compared with Fareed's `ai-eval-system` (a notebook tutorial: receipt auditor, cost model, V0 to V1). Planned Phase 8. Engineering review accepted four changes (targets as providers, cache key salt, CLI-only python targets, pytest replay). Built M1 and M2. Switched to Sonnet 5.5 at medium effort. The owner asked for the five docs and this file, and for clean structure.
