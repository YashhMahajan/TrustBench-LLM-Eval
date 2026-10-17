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
| `trustbench rescore RUN_ID` | Score stored outputs again. No model calls. |

## Settings

| Variable | Use |
|---|---|
| `TRUSTBENCH_DATABASE_URL` | Database. Default: the Postgres from `docker-compose.yml` |
| `TRUSTBENCH_LOCAL_BASE_URL` | URL of a local OpenAI-compatible server (`local:` models) |
| `TRUSTBENCH_PRICES` | JSON that sets prices: `{"model": [input, output]}` per 1M tokens |
