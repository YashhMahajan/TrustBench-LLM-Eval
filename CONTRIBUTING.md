# Contributing to TrustBench

Thank you for your interest. This guide tells you how to set up, how to check your work, and what we expect in a pull request.

## Set up

1. Install Python 3.12 or newer.
2. Clone the repository.
3. Run `make install`. It installs TrustBench and the development tools with the exact versions that we tested.
4. Run `make check`. It runs lint, the writing check, the tests and the self-test.

You need no API key and no running service. The tests use SQLite and a mock provider.
For the dashboard, install pnpm and run `make dashboard`.

## Find your way

- `docs/ARCHITECTURE.md` is the map of the code. Read it first.
- `docs/DATA.md` describes the tables and the files that you write.
- `docs/EDD.md` explains how to use TrustBench on your own app.
- `docs/ISSUES.md` lists the known limits.
- `examples/support_agent/` is a complete example. Run it with `make edd-demo`.

Put new code in a package (`agents/`, `edd/`, `ci/`, `providers/`). Do not add files to the flat root of `src/trustbench/`.
Give each file one job and a short docstring at the top.

## Make a change

1. Open an issue first for a large change. A short description is enough.
2. Make a branch from `main`.
3. Write the test first, or together with the code.
4. Run `make check`.
5. Open a pull request. Fill in the template.

## What we expect

**Tests.** Every new behaviour has a test. A new self-test check also has a test that breaks the code that the check watches.
After you write a test, break the code on purpose and make sure the test fails. A test that never failed proves nothing.

**Database changes.** Change `src/trustbench/db.py` and write a migration in `src/trustbench/migrations/versions/`.
`tests/test_migrations.py` fails until the migration matches the models.

**Writing.** Follow the writing rules in code comments, docstrings, command help text and documents.
Use up to 20 words in an instruction and up to 25 in other sentences. Use the active voice. Do not use contractions or semicolons.

Run `make writing` to check. A test runs the same check in CI.

**Security.** Never commit a key or a secret. Do not run user code on the server. Treat text from a model as untrusted.
Read `SECURITY.md` before you change the API, the Action or the app targets.

**Commit messages.** Write a short first line that says what the change does. Add a paragraph that says why.

## Report a problem

Use the issue templates for a bug or a feature request. For a security problem, follow `SECURITY.md`. Do not open a public issue.
