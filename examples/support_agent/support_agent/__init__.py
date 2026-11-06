"""A small customer-support agent in four versions, built to show Eval-Driven Development.

  v0  Answers from "memory". Calls tools with wrong arguments. Hallucinates success.
  v1  Uses its tools correctly. But it refunds any amount and loops on unknown orders.
  v2  Searches the knowledge base, escalates large refunds to a human, ignores injected instructions.
  v3  Like v2, and also understands "my parcel arrived damaged" (a gap found in production).

The "LLM" is a scripted stand-in (llm.py) so the example is deterministic and needs no API key.
Replace `parse` with a real model call to try your own: the tools, the recording and the tests stay the same.
"""
