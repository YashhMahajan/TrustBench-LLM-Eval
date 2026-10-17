# What this project teaches

Each section has a mistake that is common in fast, AI-written code, what we found in this project, how we fixed it, and one question you can expect in an interview.

## Performance

| Mistake | Where it was here | Interview question |
|---|---|---|
| **N+1 queries**: one query for each row | `report.case_rows` ran 4 queries for every execution | "A page is slow when the list grows. How do you find out why?" Count statements, look for a query inside a loop, replace it with a join or an `IN` list |
| **Hidden O(n²)** | `for_model` called a function that scanned all records, once per record. Most of the 43 s `/v1/runs` call came from this and from loading every row of every run, not from the database | "Why can a comprehension be slow?" The cost of the function inside it multiplies by the length of the loop |
| **ORM row by row** | `session.add` in a loop sends one INSERT for each row on SQLite, because it needs the new id | "How do you insert 100,000 rows?" Bulk insert, read the ids with one query by a key you know (`bulk.py`) |
| **Commit for each item** | One transaction for each model answer | "What does a commit cost?" A disk sync. Batch, but decide how much work you accept to lose (here: 2 seconds) |
| **Work at import time** | CLI start took 1.15 s because every command imported every library | "How do you make a CLI start fast?" Lazy imports. Test with `python -X importtime` |
| **Index by habit** | One index for each column. The queries filter on two columns and sort | "What is a composite index?" Column order matters: equality first, then range or sort. A composite index replaces its first column's own index |
| **Paying twice** | A re-run called the model again | "How do you make an LLM eval cheap?" Cache by a hash of everything that changes the answer. Put the repetition number in the key or you cannot measure variance |

## Security

| Mistake | What we did | Test |
|---|---|---|
| A new route forgets authentication | A test walks over every route in the app. Without a key: 401 (except `/v1/health`). With a read key, every change is 403 | `test_web_security.py` |
| Body limit checked only in one place, and only by the `Content-Length` header | A middleware counts bytes while they arrive, for every route. A chunked upload cannot fill memory | same file |
| Trusting `X-Forwarded-For` | Believed only from trusted proxies, read from the right. Without this, one attacker can write any address and avoid the lock for wrong keys, and users behind one proxy lock each other out | `client_ip` tests |
| Exception text in API answers | One handler logs the details and returns a fixed message. `gate_preview` caught `Exception` and printed it | `test_an_unexpected_error_gives_no_details...` |
| Missing headers | `nosniff`, `DENY`, `no-store`, a strict CSP for JSON, HSTS behind HTTPS (API and dashboard) | `test_api_answers_carry_security_headers` |
| Cookie `secure` flag set from `NODE_ENV` | Set from the real protocol (`x-forwarded-proto`). A secure cookie over plain HTTP on localhost is silently dropped by some browsers | dashboard `login/page.tsx` |
| Invented or unpinned dependencies | `requirements.lock`, used by the Docker image. A test checks that each dependency is installed, has a lower bound and is in the lock | `test_hygiene.py` |
| Secrets in the repository | A test scans for key patterns. `.env` is ignored and the example holds no real value | `test_hygiene.py` |
| Timing and user enumeration | A missing, wrong, revoked and expired key all give the same 401 | `test_api.py` |

## Tests that lie

- A test had the comment "the gate blocks the old behaviour" but never called the gate. A linter found the unused variable. We made the comment true.
  When we tried to make it do what it said, the gate answered "inconclusive" for one case. That is correct behavior: one case proves nothing.
- The self-test has 28 checks. Each one has a test that **breaks the code on purpose** and expects the check to fail. A check that cannot fail is decoration.
- A dead variable (`low = normalize(out)`) can mean a step was forgotten. Read what the linter says before you delete it.

## Design questions you can answer after this project

1. **Why store raw outputs and scores in different tables?** To score again with a better evaluator and no model call.
2. **Why a Newcombe interval for paired changes?** A bootstrap says "certain" when 8 of 8 cases move the same way. Newcombe stays wide for small samples.
3. **Why is a result "inconclusive" and not "pass"?** The interval includes the limit. A gate that guesses is worse than a gate that says "I need more data".
4. **Why can a run be `incomplete`?** A budget stop, a crash or Ctrl-C leaves cases unfinished. The gate refuses an incomplete run. `resume` finishes it.
5. **How do two workers never take the same job?** `UPDATE ... WHERE state = :seen AND attempts = :seen`. Only one update changes a row.
6. **Why does the cache key contain the repetition?** Repetitions exist to measure how much an answer varies. A cache would hide it.
7. **What is write-behind and what does it risk?** Results wait in memory and are saved in batches. A crash loses at most the last batch, and those cases are still `pending`, so they are run again. No half-saved case exists, because one case is one transaction.
8. **Why trust nothing from the client?** Headers, addresses, sizes, paths and ids all come from the other side. Check them at the edge, once, for every route.

## Habits to keep

- Profile before you optimise. Count before you time.
- Write the test that would have caught the bug before you fix it.
- Ask of each check: "What would make this fail?" and then do that.
- Write down what you did not test. See "Limits" in the README.
