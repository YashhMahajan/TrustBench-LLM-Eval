# TrustBench — Data

What we store, in which shape, and why. Source of truth for tables: `src/trustbench/db.py`.
Schema versions: `src/trustbench/migrations/versions/0001`–`0006`. Last updated: Phase 8, milestone 4.

## 1. Files you write

### Dataset (`.jsonl`, `.json`, `.csv`)
One test case is one object (`CaseSpec` in `datasets.py`):

```json
{
  "id": "refund-001",
  "task": "refund",
  "input": {"prompt": "Refund order A1"},
  "expected": {"answer": "..."},
  "evaluation": {"evaluators": [{"type": "tool_call"}]},
  "metadata": {"domain": "support", "difficulty": "easy", "severity": 4}
}
```

`input` is one of: `{"prompt"}`, `{"context", "question"}`, `{"messages": [{"role", "content"}, ...]}`.
Roles: `system`, `user`, `assistant`, `tool`. A plain model sees a messages input as a transcript. An app target also gets the full input.

Fields in `expected` by evaluator:

| Evaluator | Reads |
|---|---|
| `exact_match` | `answer` or `answers` |
| `contains` | `required_facts` (or `values` in the spec) |
| `numeric` | `number` |
| `abstain` | `abstain` (true/false) |
| `grounded_claims` | `input.context` or `expected.evidence` |
| `tool_call` | `tool_calls: [{"name", "args": {key: value-or-matcher}}]`. An empty list means that the app must call no tool. |
| `trajectory` | `trajectory: ["tool", ...]` and spec `order`, `max_steps`, `max_repeats` |
| `task_success` | `final_state: {"path": value}` or spec `checks` |
| `rag_*` | `required_facts` or `answer` (recall). Documents come from the `retrieval` span or `input.context`. |

Matchers for an argument value: a plain value, `{"approx", "tol"/"rel"}`, `{"regex"}`, `{"in"}`, `{"contains"}`, `{"type"}`, `{"schema"}`, `{"any": true}`.
Bad fields fail when the dataset loads.

### `trustbench.yaml`  (`edd/config.py`)
`version`, `dataset`, `targets` (name → spec), `default_targets`, `judge`, `policy`, `baseline`, and `run` (`reps`, `concurrency`, `retries`, `timeout`, `budget`, `rpm`, `cache`, `system_prompt_file`).
Unknown keys are errors. Write one with `trustbench init`.

### `policy.yaml`  (`policy.py`)
The release rules: `min_cases`, `confidence_bound`, `on_insufficient_evidence`, `min_effect`, `overall`, `dimensions`, `critical_failures_allowed`, `latency_p95_max_seconds`, `cost_per_success_max_usd`, `call_failure_rate_max`, `annual_cost_max_usd` and its `cost_model` block.

The cost model (`edd/costmodel.py`): `volume_per_year`, `positive_rate`, `costs` (`false_positive`, `false_negative`, `true_positive`, `true_negative`), `label_key` (default `positive`), `processing_cost_per_item`, `manual` (`fp_rate`, `fn_rate`, `cost_per_item`).
Mark each test case with metadata `positive: true` or `false`. A failed positive case is a miss. A failed negative case is a false alarm.

## 2. App target records

Recorded by `providers/recorder.py`. Stored as JSON on `model_responses` and `response_cache`.

```json
// tool_calls (in call order)
[{"name": "refund", "args": {"order_id": "A1"}, "result": {"status": "ok"}, "error": null}]

// spans
[{"kind": "retrieval", "name": "search_kb", "data": {"docs": ["...", {"id": "d2", "text": "..."}]}, "ms": 12.3, "error": null}]
```

Span kinds: `model`, `retrieval`, `tool`, `other` (the same kinds as production traces).

The API returns both for a case: `GET /v1/runs/{id}/cases/{case_id}` has `tool_calls` and `spans` for each execution (null for a plain model). `GET /v1/experiments` lists the variants with their pass rates and the verdict on each hypothesis.

An app target model string: `python:package.module:function@label` or `http:https://host/path@label`.
The text after the last `@` is the version label (not part of the function name or URL path).

## 3. Tables

Relations:

```
workspaces ─┬─ api_keys
            ├─ dataset_versions ── test_cases
            ├─ evaluation_runs ── test_executions ── model_responses ── evaluation_results
            │         │                  │                   └── human_annotations
            │         │                  └── model_configurations
            │         └── release_decisions ── baselines
            ├─ experiments (run_id, parent_id → experiments)
            ├─ proposed_cases
            ├─ production_traces ── trace_spans, trace_evaluations
            ├─ audit_log
            └─ jobs
response_cache  (shared answer cache, keyed by a hash)
```

| Table | Purpose | Key columns |
|---|---|---|
| `dataset_versions` | An immutable version of a dataset | `name`, `content_hash`, `case_count` |
| `test_cases` | The cases of a version | `case_id`, `task`, `input`, `expected`, `evaluation`, `meta` |
| `model_configurations` | A model and its settings | `config_hash`, `provider`, `model`, `params`, `system_prompt` |
| `evaluation_runs` | One run | `status`, `config_snapshot`, `software_version`, `total_cost_usd`, `judge_cost_usd`, `overview` |
| `test_executions` | One case × model × repetition | `repetition`, `status` (pending, done, failed, skipped) |
| `model_responses` | One attempt. A retry is a new row. | `ok`, `output`, `error`, tokens, `latency_ms`, `cost_usd`, **`tool_calls`**, **`spans`** |
| `evaluation_results` | One score of one response | `evaluator`, `rubric_version`, `passed`, `score`, `rationale`, `details`, `batch` |
| `human_annotations` | Human labels for judge validation | `annotator`, `dimension`, `label` |
| `response_cache` | A stored answer for a request key | `key`, `text`, tokens, `latency_ms`, **`tool_calls`**, **`spans`** |
| `baselines` | A name for a run | `name`, `run_id`, `model` |
| `experiments` | A named variant with its run, parent and hypothesis. Never changed. | `name`, `run_id`, `model`, `parent_id`, `hypothesis` |
| `release_decisions` | A saved gate decision | `policy_snapshot`, `status`, `allowed`, `checks` |
| `proposed_cases` | A new test case waiting for review | `spec`, `source_run_id`, `status`, `expected_verified` |
| `production_traces`, `trace_spans`, `trace_evaluations` | Production traffic (redacted) | `input_enc`, `output_enc`, `redactions`, `expires_at`, `feedback` |
| `workspaces`, `api_keys`, `audit_log` | Tenants, keys (hashed, with role and expiry), audit | |
| `jobs` | Background tasks | `kind`, `state`, `attempts`, `lease_expires_at` |

Bold columns arrived in migration 0005. The `experiments` table arrived in migration 0006.

TrustBench keeps the system prompt of a run in `evaluation_runs.config_snapshot.options.system_prompt`. It keeps the text, not only a hash.

## 4. The cache key

`engine.cache_key` = SHA-256 of `[provider, model, prompt, system, temperature, max_tokens, repetition]`, plus the **salt** when it is not empty.

| Target | Salt |
|---|---|
| A model (`openai:...`) | empty (keys are the same as before Phase 8) |
| `python:...@label` | `v=<label>\|git=<HEAD commit>` |
| `python:...` clean git tree | `git=<HEAD commit>` |
| `python:...` dirty tree or no git, no label | none. TrustBench does **not** cache the answer. |
| `http:...@label` | `v=<label>\|url=<hash of URL>` |
| `http:...` without a label | none. Not cached. |
| `mock` and other non-cacheable providers | none. Not cached. |

TrustBench never caches a failed call.

## 5. Privacy and retention

- Evaluation runs store outputs as they are. Do not put secrets in test data.
- Production traces: TrustBench redacts personal data and secrets before it stores them (`redact.py`, pattern based). You can encrypt the content (`crypto.py`).
- Each workspace has a data policy (`dataprotect.py`): what to keep and for how long. `trustbench traces purge` and the worker delete old traces.
- TrustBench writes every change to keys or settings to `audit_log`.

## 6. Rules for changing the data model

1. Change `db.py`. Write a migration in `migrations/versions/`.
2. `tests/test_migrations.py` fails until the migration matches the models.
3. Never edit a stored dataset version. Make a new version.
4. Keep raw outputs and scores in separate tables.
