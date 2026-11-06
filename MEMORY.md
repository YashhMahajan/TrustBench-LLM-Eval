# MEMORY.md: TrustBench project memory

Read this file first in every session. It holds what you must remember about the project.
Long-term memory changes rarely. Update short-term memory at the end of each work session.
Related docs: `docs/PRD.md` (what and why), `docs/ARCHITECTURE.md` (where), `docs/DATA.md` (data), `docs/TASKS.md` (work), `docs/ISSUES.md` (limits), `docs/EDD.md` (how to use it).

---

# Long-term memory

## What this project is

TrustBench evaluates LLMs and LLM apps. A user sets an API key, or points at their own function or service. TrustBench then gives pass rates with intervals, a release gate, failure analysis and a production loop.
It ships to engineers for their own workflows. It is also the owner's showcase project for a resume and for interviews. Quality and clarity matter more than the number of features.

## Stack

- Python 3.12, FastAPI, SQLAlchemy and Alembic, Pydantic, Typer and Rich.
- httpx for HTTP. The project uses no vendor SDKs.
- PostgreSQL in production. SQLite in tests.
- A Next.js dashboard (pnpm).
- pytest and ruff.
- We wrote the statistics by hand in `stats.py`. The project uses no numpy or scipy.

## Commands

| Task | Command |
|---|---|
| Run all tests | `python -m pytest -q` (about 70 seconds) |
| Lint | `ruff check src tests examples` |
| Self-test | `trustbench selftest` (`--quick` is faster) |
| Check the writing rules | `make writing` |
| Try it with no key | `trustbench run --model mock:smoke` |
| Test your own function | `trustbench run --model python:pkg.mod:fn@v1` |
| See the whole EDD story | `make edd-demo` |

## Rules of the project (the owner's wishes)

1. Push to GitHub only when the owner says so. The owner said so for the Phase 8 work after the writing rewrite.
2. Write commit messages like a human. Do not add AI trailers.
3. Keep the code clean and easy to navigate. Put new code in a package (`agents/`, `edd/`, `ci/`, `providers/`), not in the flat root. Use one concern in each file. Start each file with a short docstring.
4. Follow the writing rules in all project text. This covers docs, docstrings, comments and command help text. Use up to 20 words in an instruction and up to 25 in other sentences. Use the active voice. Do not use semicolons or contractions. A test (`tests/test_writing_rules.py`) enforces this.
5. Give every new behaviour a test. Give every new selftest check a test that breaks the code it watches. After you write a test, break the code on purpose and make sure the test fails.
6. Keep `docs/` current. Update `TASKS.md` at each milestone. Update `ISSUES.md` when you find a limit. Update `DATA.md` when the data changes. Update `ARCHITECTURE.md` when you add a module.
7. Do not add interview-prep or learning-guide files. The owner will ask for them later.
8. Say clearly what we did not verify. See `docs/ISSUES.md`, items I-01 to I-05 and I-30.

## Design decisions and the reasons

| Decision | Reason |
|---|---|
| App targets are `Provider` subclasses (`providers/app.py`) | The engine, cache, resume, rate limit and budget work without change. The diff stays small. |
| The cache key has a salt: a version label or the git commit | A stale answer would silently corrupt an experiment comparison. |
| TrustBench does not cache a dirty tree without a label | We cannot tell if the code is the same. |
| `python:` and `http:` targets are off unless the CLI or pytest turns them on | A server must never run user code. A test walks the server modules. |
| The pytest plugin replays stored answers by default | A normal `pytest` run must be free and fast. Live calls need `--trustbench-live`. |
| A plugin cannot replace a built-in evaluator | A third-party package must not change how existing scores work. |
| TrustBench reports a broken evaluator and leaves it out | A broken checker says nothing about the model. |
| An unknown cost is `incomplete`, never zero | Zero would make an unpriced model look free. |
| Tool-call matching uses a search, not a greedy pass | A loose expectation that comes first must not take the call that a strict one needs. |
| The Action gives the token only to the step that posts the comment | The step that runs your app must not be able to read the token. |

## Invariants

`docs/ARCHITECTURE.md` section 6 lists them. In short:
- A dataset version never changes.
- A run stores its config.
- Responses and scores live in separate tables.
- A retry is a new row.
- An incomplete run says so.

## Gotchas

- `evaluators.py` imports the evaluator modules at the end of the file. They need `register`. Keep that order.
- `tests/conftest.py` turns app targets off after every test. A test that needs them calls `allow_app_targets()`.
- A synchronous Python target runs inside the event loop. Keep the function fast, or make it `async`.
- `sentence_claims` ignores sentences of fewer than 3 words. Short test outputs skip the claim checks.
- The shell can drift into `src/`. Use absolute paths.
- zsh does not split an unquoted `$VARIABLE` into words. Use `xargs` or quote the globs.

---

# Short-term memory (update each session)

## Current state: Phase 8 (EDD), 2026-11-06

- Branch `main`. All six Phase 8 milestones are done.
- The `/cso` security scan is done. We fixed all 6 findings (see `docs/ISSUES.md`, "Security scan").
- We rewrote all project text to follow the writing rules and added `scripts/writing_check.py` and a test for it.
- The owner approved the push after the rewrite. Check `git status` and `git log origin/main..HEAD` for the real state.
- The CI workflow runs on GitHub. The reusable Action has not run on a real pull request yet (I-30). Do not start Docker on this machine (the owner said no). CI builds the image.

## Next steps

1. Open a test pull request that uses the Action, and fix what it shows (I-30).
2. Turn on "Require review from Code Owners" in the branch protection of main (I-40).
3. Verify the Postgres migrations in CI with a Postgres service container (I-02). Do not use Docker locally.
4. Run one live evaluation against a real model and record it in `docs/PERFORMANCE.md` (I-01).

## Open questions for the owner

- Do we move the old flat modules into packages? This is a bigger cleanup. It carries risk. It needs its own commit.

## Session log

- 2026-11-06: We compared TrustBench with the `ai-eval-system` notebook of Fareed Khan. We planned Phase 8 and built all six milestones. We added the five PM docs. We ran `/cso` and fixed 6 findings. We rewrote all project text to follow the writing rules.
