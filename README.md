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

A judge is a model that grades open answers. Choose a strong model that is not the model under test:
`trustbench run --dataset hallucination --judge anthropic:claude-sonnet-5-5`

A judge can be wrong. Label some answers by hand, then run `validate-judge` to see how much to trust it.

## Settings

| Variable | Use |
|---|---|
| `TRUSTBENCH_DATABASE_URL` | Database. Default: the Postgres from `docker-compose.yml` |
| `TRUSTBENCH_LOCAL_BASE_URL` | URL of a local OpenAI-compatible server (`local:` models) |
| `TRUSTBENCH_PRICES` | JSON that sets prices: `{"model": [input, output]}` per 1M tokens |
