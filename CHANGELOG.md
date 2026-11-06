# Changelog

All notable changes to this project are in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- App targets: test your own Python function (`python:`) or HTTP service (`http:`), with a cache key that follows the version of your code.
- Recording of tool calls and spans while an app runs, with replay from the cache.
- Evaluators for agents: `tool_call`, `trajectory`, `task_success`, and the judge checks `rag_faithfulness`, `rag_answer_relevance`, `rag_context_precision` and `rag_context_recall`.
- Conversations as test inputs, and checks that fail when a dataset loads with wrong agent fields.
- `trustbench.yaml` and `trustbench init`. `trustbench run` reads the file.
- A pytest plugin that replays stored answers by default and calls models only with `--trustbench-live`.
- Evaluator plugins through the `trustbench.evaluators` entry point group.
- Experiments: a history of variants with a hypothesis, and a verdict against the parent variant.
- A business cost model with an interval, and the gate rule `annual_cost_max_usd`.
- System prompt files, stored with the run, and a prompt diff in `experiment compare`.
- `trustbench ci check`, `ci comment` and `ci post`, and a reusable GitHub Action that comments on pull requests.
- A CI workflow for this repository.
- A dashboard page for the variant history, and a view of the steps that an agent took.
- A support-agent example and `make edd-demo`.
- Documents: product requirements, architecture, data, tasks, known issues, and a guide to Eval-Driven Development.
- A check of the writing rules (`make writing`).

### Changed
- Run steps that the CLI, CI and pytest share now live in `edd/runner.py`.
- The `trustbench` command puts the current folder on the import path, after the standard library.

### Security
- Pinned `certifi` and `filelock` to fixed versions.
- The Action gives the token only to the step that posts the comment, and installs from the pinned lockfile.
- The pull request comment escapes text from a model.

## [0.1.0]

First version: the core engine, scoring, statistics and release gate, robustness and failure analysis, the production loop, the dashboard, and the self-test.
