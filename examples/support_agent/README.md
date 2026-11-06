# Support agent: an Eval-Driven Development example

A customer-support agent with tools (`lookup_order`, `refund`, `escalate_to_human`, `delete_account`, `search_kb`) in four versions.
It runs with **no API key**: the "model" is a scripted stand-in (`support_agent/llm.py`) so every run is the same.
Replace `parse` with a real model call to try your own. The tools, the recording and the tests stay the same.

```bash
make edd-demo          # from the repository root. About 20 seconds. Uses a temporary database.
```

| Version | What changed | Pass rate | Gate |
|---|---|---|---|
| v0 | The simplest thing: answers from memory, calls tools with wrong arguments | 3 of 48 | – |
| v1 | Uses tools correctly | 29 of 48 (**better**, hypothesis supported) | **fail**: refunds over the limit, 12 of 14 critical cases fail, yearly cost $2.4M over the $900k limit |
| v2 | Knowledge base, human escalation, ignores injected text | 48 of 48 | **pass** |
| v3 | Understands "arrived damaged" (found in production) | 49 of 49 (the new case was promoted from a production trace) | **pass** |

Files: `support_agent/` (the agent), `datasets/support.jsonl` (48 cases, built by `build_dataset.py`), `trustbench.yaml`, `policy.yaml` (release rules and cost model), `demo.py` (the story).

Try the parts yourself, from this folder:

```bash
trustbench run --target v1
trustbench failures RUN_ID
trustbench gate check RUN_ID --baseline OTHER_RUN_ID
trustbench cost RUN_ID
```
