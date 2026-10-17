# TrustBench

TrustBench tests LLMs. You set an API key. TrustBench does the rest.

## Start

1. Start the database: `docker compose up -d`
2. Install: `pip install -e .`
3. Set a key: `export ANTHROPIC_API_KEY=...` (or `OPENAI_API_KEY`, `GEMINI_API_KEY`)
4. Run: `trustbench run`

No key? Try the demo: `trustbench run --model mock:smoke`

## Commands

| Command | Action |
|---|---|
| `trustbench run` | Run a dataset against your models |
| `trustbench results RUN_ID` | Show the result of each case |
| `trustbench show RUN_ID CASE_ID` | Explain one case: answer, scores, reasons, claims |
| `trustbench rescore RUN_ID` | Score stored outputs again. No model calls. |
| `trustbench check DATASET` | Find duplicates, conflicts, missing references, personal data |
| `trustbench annotations export RUN_ID` | Write a CSV for human labels |
| `trustbench annotations import FILE --annotator NAME` | Store the human labels |
| `trustbench validate-judge RUN_ID` | Compare judge grades with human labels |
| `trustbench judge-bias --judge provider:model` | Test a judge for position, length and wording bias |
| `trustbench report RUN_ID` | Pass rates with intervals, weak slices, latency, cost, claims |
| `trustbench compare RUN --baseline REF` | Change against a baseline, with interval, p-value and verdict |
| `trustbench gate check RUN` | Check a run against `policy.yaml` |
| `trustbench baseline set NAME RUN_ID` | Name a run as a baseline |
| `trustbench policy init` | Write a policy file with comments |
| `trustbench calibration RUN_ID` | Brier score, calibration error, AUROC, risk and coverage |
| `trustbench robustness build DATASET` | Add changed copies of each case (typos, format, moved evidence, paraphrase) |
| `trustbench robustness report RUN_ID` | Break rate, degradation, consistency, weakest transform |
| `trustbench failures RUN_ID` | How cases failed, recurring patterns, retrieval stage, what to fix first |
| `trustbench proposals generate RUN_ID` | Propose new test cases from failures |
| `trustbench proposals list / approve / edit / reject / export` | Review the proposals. Export the approved ones as a dataset |
| `trustbench frontier RUN_ID...` | Compare models on quality, cost and latency |
| `trustbench workspace create / list / set` | Tenants and their data policy |
| `trustbench key create / list / revoke` | API keys (read, write, admin) |
| `trustbench serve` | Start the HTTP API (docs at `/docs`) |
| `trustbench worker` | Run background tasks (online evaluation, deletion of old traces) |
| `trustbench monitor` | Production state: errors, latency, cost, online checks |
| `trustbench drift` | Has the production traffic changed? |
| `trustbench traces list / show / flagged / promote / purge` | Production traces and their conversion to tests |
| `trustbench jobs status / dead / retry` | The task queue |
| `trustbench secrets generate-key` | Encryption key for stored content |

## Decide if a model is ready

1. Run your dataset, then name the run that works today as the baseline:
   `trustbench baseline set main RUN_ID`
2. Change your model or prompt. Run the dataset again.
3. See what changed: `trustbench compare NEW_RUN --baseline main --slices`
4. Write a policy once: `trustbench policy init`. Edit `policy.yaml`.
5. Ask the gate: `trustbench gate check NEW_RUN --baseline main`
   Exit code 0 means the release may go on. Exit code 1 means it may not.

`trustbench report RUN_ID` shows pass rates with intervals, weak slices, latency, errors and cost. Add `--json` for other tools.

### How to read the results

Every pass rate has a 95% interval and the number of cases. A small run gives a wide interval. The gate uses the interval, not only the average.

| Verdict | Meaning |
|---|---|
| better / worse | The change is real and larger than `min_effect` |
| slightly better / slightly worse | The change is real but may be too small to matter |
| no real change | The change is smaller than `min_effect` |
| cannot tell | The data is too weak. Run more cases. |

| Gate status | Meaning |
|---|---|
| pass | Every rule is met, with enough evidence |
| fail | At least one rule is clearly broken |
| inconclusive | The evidence is too weak to decide. This blocks the release by default. |
| incomplete | The run did not finish, or an evaluator broke |

A failed model call counts as a failed case. A skipped case or a broken evaluator is left out and reported, because it says nothing about the model.

## Find weak spots

**Is the model stable?** `trustbench robustness build grounded` writes `grounded_robust.jsonl`: the original cases plus copies with extra spaces, a different wrapper, typos, shuffled sentences or the answer moved or hidden. Every copy is checked, so the right answer does not change. Run that file, then `trustbench robustness report RUN_ID`.

A model that passes the original but fails a copy is fragile. The report keeps four numbers apart: break rate, degradation, consistency (a model can be consistent and consistently wrong) and the weakest transform.

**Why did cases fail?** `trustbench failures RUN_ID` gives each failed case labels (wrong answer, unsupported claim, followed an injection, ...), groups similar failures, says if the evidence reached the model, and ranks what to fix first. The ranking shows its four factors: severity, frequency, exposure and detectability. Patterns show what failures share. They do not prove a cause.

**Keep old failures fixed.** `trustbench proposals generate RUN_ID` proposes new cases from failed ones. Nothing joins a dataset until a person approves it. Answers that a model wrote must be checked by a person first. `trustbench proposals export DATASET` writes the approved cases as a new dataset version.

**Which model is worth its price?** `trustbench frontier RUN_ID --min-quality 0.9 --max-cost 0.01` lists the models that nobody beats on quality, cost and latency together. A model with no known price is listed apart. An unknown cost is never counted as zero.

The `injection` suite checks only what the model writes. It does not test your permissions, your sandbox or your data limits.

## Watch a system in production

1. Make a workspace and a key, then start the server:
   ```bash
   trustbench workspace create acme
   trustbench key create --workspace acme --role write      # Copy the key. It is shown once.
   trustbench serve
   ```
2. Send traces from your code:
   ```python
   from trustbench.sdk import TrustBench
   tb = TrustBench()      # uses TRUSTBENCH_API_KEY and TRUSTBENCH_URL

   with tb.trace("support-bot", model="gpt-4o-mini") as t:
       t.input = question
       with t.span("search", kind="retrieval"):
           docs = search(question)
       t.output = answer(question, docs)
   tb.feedback(t.trace_id, -1)      # A user did not like the answer
   ```
   The SDK never raises an error into your code, never waits for the network, and removes personal data before it sends anything.
3. Start a worker for the background tasks: `trustbench worker`
4. Look at it: `trustbench monitor`, `trustbench drift`, `trustbench traces flagged`

### What we store

The default is the safest. A workspace stores **measurements only**: time, size, tokens, cost, status. It stores **no text**.
To keep text, an admin turns it on. The text is then redacted (emails, phone numbers, cards, secrets and more are replaced by markers) and encrypted with your key before it reaches the database.

```bash
trustbench secrets generate-key                 # put the result in TRUSTBENCH_SECRET_KEY
trustbench workspace set acme --store-content --retention-days 14 --sample-rate 0.1
```

Old traces are deleted after the retention time. Reading stored text needs an admin key, and every read is written to an audit log. A key sees only its own workspace.

### Online checks and what they mean

A sample of the traffic is checked while it runs. With no reference answers, only some checks are possible, and each result says how sure we are:

| Kind | Example | Meaning |
|---|---|---|
| measured | word limit, JSON schema, forbidden words | A rule found it. No guess. |
| estimated | groundedness, helpfulness | A judge model guessed. A judge can be wrong. |
| not measured | answer correctness | Production has no reference answers. `trustbench monitor` lists these gaps. |

User ratings are a signal from the users who chose to rate. They do not stand for all traffic.

### From a production failure to a guard

`trustbench traces flagged` lists traces with an error, a bad rating or a failed check. `trustbench traces promote TRACE_ID` proposes a test case from one of them. A person approves it (`trustbench proposals approve`), `trustbench proposals export` writes the dataset, and the next release must pass it.

### Scale

Tasks wait in the database by default. For many workers on a busy queue set `TRUSTBENCH_REDIS_URL` (and `pip install 'trustbench[redis]'`). Start as many `trustbench worker` processes as you need.

### Limits

- Redaction finds patterns (emails, numbers, secrets). It does not find names or addresses in free text.
- The server has no TLS, rate limit or key expiry. Put it behind a proxy.
- The Redis queue is tested against an in-memory copy of the Redis commands it uses, not against a Redis server.
- Runs and datasets from `trustbench run` are not split by workspace yet. The API serves production data only.

## Built-in suites

Use a name with `--dataset`. You can also give the path to your own `.jsonl`, `.json` or `.csv` file.

| Suite | Tests | Needs `--judge` |
|---|---|---|
| `smoke` | Five quick cases for a first try | No |
| `factual` | Short factual questions | No |
| `grounded` | Answers from a given context | No |
| `abstention` | Say "I do not know" when the context has no answer, and answer when it has | No |
| `instruction_following` | Word limits, format, forbidden words | No |
| `structured_output` | Valid JSON that matches a schema | No |
| `safety` | Refuse harmful requests, answer safe ones | No |
| `calibration` | Answer plus stated confidence | No |
| `hallucination` | False premises, invented sources, wrong sources | Yes |
| `injection` | Ten hidden instructions in documents and tool results | No |

A judge is a model that grades open answers. Choose a strong model that is not the model under test:
`trustbench run --dataset hallucination --judge anthropic:claude-sonnet-5-5`

A judge can be wrong. Label some answers by hand, then run `validate-judge` to see how much to trust it.

## Settings

| Variable | Use |
|---|---|
| `TRUSTBENCH_DATABASE_URL` | Database. Default: the Postgres from `docker-compose.yml` |
| `TRUSTBENCH_LOCAL_BASE_URL` | URL of a local OpenAI-compatible server (`local:` models) |
| `TRUSTBENCH_PRICES` | JSON that sets prices: `{"model": [input, output]}` per 1M tokens |
