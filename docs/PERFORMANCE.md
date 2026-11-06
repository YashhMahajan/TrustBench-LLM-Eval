# Performance

All numbers come from one script on one laptop (SQLite file, mock provider, 20,000 cases for the run tests, 5,000 traces).
Run times change from machine to machine. Counts of SQL statements do not, so the tests in `tests/test_performance.py` and
`tests/test_engine_scale.py` count work (statements, calls, imports) and never time anything.

## Before and after

| What | Before | After | Cause and fix |
|---|---|---|---|
| Whole benchmark (20,000 cases) | 139 s | 19.7 s | All items below |
| `GET /v1/runs` (5 runs of 20,000 cases) | 43 s | 0.03 s | The list loaded every row of every run. Each run now stores a small overview (migration 0004) |
| Run 20,000 cases (mock provider) | 16.9 s | 2.7 s | One INSERT for each execution and each result, one commit for each. Now: bulk insert and write-behind batches |
| SQL statements for a 2,000 case run | 10,007 | 177 | Same fix. Statements now grow with the number of batches, not with the number of cases |
| Report rows for 20,000 cases | 3.1 s | 1.0 s | N+1 queries (one query for each row). Now a fixed number of queries |
| Dataset check (20,000 cases) | 4.1 s | 0.27 s | Near-duplicate search compared every pair (O(n²)). Now an index of word groups, then an exact check on the few candidates |
| Ingest 5,000 traces | 1.5 s | 0.8 s | About 900 statements for a small batch (one INSERT for each row). Now bulk inserts and one query for the ids |
| `trustbench --help` | 1.15 s | 0.22 s | Every command imported every heavy library at start. Heavy imports moved inside the commands |
| Choosing a model from a run | O(n²) | O(n) | A list comprehension called a function that scanned all records, for each record. Now cached |

## Rules we follow

1. **Measure first.** Each fix above began with a profile, not with a guess. The slow line was often not the one we expected.
2. **Count work, not seconds.** A test that says "at most 60 statements" fails on every machine for the same reason.
3. **Batch the database.** Round trips cost more than rows. Write-behind keeps one transaction for many results. TrustBench saves a result whole or not at all.
4. **Index what you query, and no more.** Four composite indexes (migration 0003) replace single-column ones they cover. Tests read `EXPLAIN QUERY PLAN`
   and fail on a table scan or on an index that another index already covers.
5. **Never repeat paid work.** The answer cache (key = provider, model, prompt, system prompt, temperature, max tokens, repetition) makes a repeated run free.
   `trustbench resume` finishes a stopped run without calling a finished case again. The self-test checks both.
6. **Be kind to the provider.** `--rpm` spaces the calls. TrustBench obeys `Retry-After` (up to 60 s). Retries use jitter. Connections close at the end.
7. **Pay at the right time.** TrustBench computes the overview once, when a run ends. Old runs get it the first time you list them.

## How the engine writes

```
asyncio tasks (concurrency N) ──> _Finished(attempts, scores, status) ──> buffer ──> one transaction
                                                                          every 50 results or 2 seconds, and on Ctrl-C
```

If the process dies, TrustBench loses at most the last buffer. Those cases still have the status `pending`, so `trustbench resume RUN_ID` runs them.

## What we did not do

- No Redis for the cache. The database already holds the answers, and a second service costs more than it saves for one team.
- No native extensions (Rust, C). Python spends its time waiting for providers, not computing.
- Statistics (bootstrap, permutation) take 0.1 to 0.2 s for 1,000 cases. That is fast enough, so we left them clear and simple.
