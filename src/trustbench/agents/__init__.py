"""Evaluators for agents and multi-turn apps: what the app did, not only what it said.

  matchers.py          How a test case describes an expected value (exact, tolerance, regex, schema, ...)
  eval_tool_call.py    `tool_call`     Did the app call the right tools with the right arguments?
  eval_trajectory.py   `trajectory`    Did it take the right steps, in a sensible order, without looping?
  eval_task_success.py `task_success`  Is the end state right (checked in code, no model needed)?
  eval_rag_judged.py   `rag_faithfulness`, `rag_answer_relevance`, `rag_context_precision`,
                       `rag_context_recall`   Judge-model checks on retrieval-augmented answers.

The tool calls and spans come from the app target (see providers/app.py and providers/recorder.py).
Importing this package registers the evaluators. evaluators.py does that at its end.
"""

from . import eval_rag_judged, eval_task_success, eval_tool_call, eval_trajectory  # noqa: F401
