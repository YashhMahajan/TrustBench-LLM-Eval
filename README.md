# TrustBench

TrustBench tests LLMs. You set an API key. TrustBench does the rest.

## Start

1. Start the database: `docker compose up -d postgres` (or everything: see “Run everything with Docker”)
2. Install: `pip install -e .`
3. Set a key: `export ANTHROPIC_API_KEY=...` (or `OPENAI_API_KEY`, `GEMINI_API_KEY`)
4. Run: `trustbench run`

No key? Try the demo: `trustbench run --model mock:smoke`

## Commands

| Command | Action |
|---|---|
| `trustbench run` | Run a dataset against your models. Useful flags: `--rpm N` (rate limit), `--budget USD`, `--no-cache`, `--reps N` |
| `trustbench results RUN_ID` | Show the result of each case |
| `trustbench show RUN_ID CASE_ID` | Explain one case: answer, scores, reasons, claims |
| `trustbench rescore RUN_ID` | Score stored outputs again. No model calls. |
| `trustbench resume RUN_ID` | Finish a run that stopped (Ctrl-C, crash, budget). Finished cases are not called again. |
| `trustbench cache stats / clear` | Stored model answers. A repeated request costs nothing. |
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
| `trustbench selftest` | Check that TrustBench measures correctly |
| `trustbench export RUN_ID` | A report of a run to send to someone |
| `trustbench db status / check / upgrade / sql` | Database versions |
| `trustbench key create --expires-days N` | A key that stops working after N days |

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

## The dashboard

A web dashboard shows everything above: release readiness, runs with intervals, the cases behind every number, comparisons, the gate history, production monitoring, drift, proposed tests to review, and the cost frontier. It runs as its own small server (Next.js) and talks to the API.

Try it with sample data (no API keys needed):

```bash
pip install -e .
trustbench demo                      # makes a "demo" workspace and prints an API key
trustbench serve                     # the API, on port 8000
cd dashboard && pnpm install && pnpm dev     # the dashboard, on port 3000
```

Open http://localhost:3000 and paste the key. The demo models are made up. The data shows how TrustBench works. It says nothing about real models.

For your own data, use your own workspace and key: `trustbench key create --workspace default --role read`. Results from `trustbench run` belong to the workspace `default`.

| Setting | Use |
|---|---|
| `TRUSTBENCH_API_URL` | Where the dashboard finds the API. Default `http://127.0.0.1:8000` |

How to read the screens: every rate is drawn as a bar with its 95% interval and its number of cases. A short bar is a sure result. A solid bar is measured, a hatched bar is a judge model's estimate, and a dotted outline is something nobody measured. Verdicts are stamps: pass, fail, inconclusive, incomplete. The key is kept in a cookie that scripts in the browser cannot read.

Only an admin key can change settings. A read key cannot approve tests. Keys are made and revoked in a terminal.

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
- The server has no TLS. Put it behind a proxy. Rate limits and the lock after wrong keys are counted in one server process: with several API processes, each counts for itself.
- The Redis queue is tested against an in-memory copy of the Redis commands it uses, not against a Redis server.
- PostgreSQL is tested only as far as the generated SQL (every table, index and key query compiles for PostgreSQL). It was not run against a PostgreSQL server.
- The Docker files were written and the compose file was validated, but the images were not built here.

## Check that TrustBench itself is right

A reliability tool must show that its own numbers are valid. `trustbench selftest` builds data where the truth is known and runs the real code on it:

- a 95% interval contains the truth about 95% of the time, also for small samples and extreme cases
- two equal systems are rarely called different. A real drop is found. False discoveries stay under control
- every evaluator agrees with a gold set. Planted personal data is removed. Ordinary text is not damaged
- each kind of injected failure (invalid JSON, a faulty prompt, a wrong reference, lost evidence, abstention) is named, blamed on the right stage and never allowed through the gate
- test copies never change the right answer. Planted dataset defects are found
- the rules that make results auditable hold. Workspaces cannot read each other. Retention is exact. No task is lost when a worker dies
- a repeated or resumed run never pays for the same model call twice

The targets are fixed from theory before the numbers are seen. The checks are themselves tested by breaking the code on purpose: each check must fail when the code it watches is wrong. The self-test has already found two real flaws, which are fixed: PSI drift was too high on small samples, and a bootstrap interval claimed certainty when 8 of 8 cases moved the same way.

```bash
trustbench selftest            # about 2 seconds. Add --quick for fewer simulations, --area statistics for one area, --json for a machine
```

## Database versions

The tables are versioned with Alembic. Commands that open a real database create it or upgrade it. Check the state with `trustbench db status`, see differences with `trustbench db check`, and give a database administrator the PostgreSQL SQL with `trustbench db sql`. A database that is newer than the program is refused. Changing a model needs a new file in `src/trustbench/migrations/versions/`: a test fails if you forget.

## Run everything with Docker

```bash
cp .env.example .env                 # set POSTGRES_PASSWORD and TRUSTBENCH_SECRET_KEY
docker compose up -d                 # database, API, worker and dashboard
docker compose exec api trustbench demo      # optional: sample data and a key
```

Open http://localhost:3000. The ports are open on your machine only. For many workers add `--profile scale` and set `TRUSTBENCH_REDIS_URL`.

## Share a run

`trustbench export RUN_ID` writes a report that someone who was not there can read: the numbers with their intervals, how cases failed, release decisions, and how to repeat the run. The dashboard has the same under “Download report”. Add `--format json` for a machine.

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
| `TRUSTBENCH_TRUSTED_PROXIES` | Proxies (addresses or networks, separated by commas) whose `X-Forwarded-For` header we believe. Default: none. Set it when a proxy or the dashboard sits in front of the API, or all visitors look like one address |
| `TRUSTBENCH_RATE_LIMIT` | Requests for each API key each minute. Default 600. 0 turns it off |

## Speed

A 20,000-case run is saved in 2.7 seconds (it was 17). A list of runs answers in 30 ms (it was 43 s). Details, numbers and the reasons are in [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

## What it does well, and what it does not

**Strong:** statistics you can defend (Wilson and paired intervals, McNemar, multiple-test control), a release gate that says "inconclusive" or "incomplete" instead of guessing,
judge validation against human labels, robustness tests, calibration, failure analysis by stage, a loop from production to tests, privacy by default (measurements only, redaction, encryption),
multi-tenant keys, and a self-test that proves the numbers and is itself tested by breaking the code.

**Narrower than other tools:** no multi-turn or agent tests, fewer public benchmarks than lm-evaluation-harness or Inspect AI, no red-team prompt generator, no declarative YAML test file like Promptfoo,
and no community or plugin ecosystem.

**Not verified here:** a call to a live provider (no key was used), a real PostgreSQL server, a real Redis server, the Docker images (the files are checked, not built), and the changed dashboard files
(the cookie flag, forwarded address and security headers were written but the dashboard was not rebuilt). Run `trustbench selftest` and `pytest` first on your machine, then try one small real run.
