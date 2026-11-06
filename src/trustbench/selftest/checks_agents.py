"""Do the agent evaluators, the span recorder and the app-target cache key behave?"""

import asyncio

from ..engine import cache_key
from ..evaluators import evaluate
from ..providers import GenRequest
from ..providers.app import PythonAppProvider, code_fingerprint
from ..providers.recorder import span, tool_call
from . import Ctx, check


def c(name, **args):
    return {"name": name, "args": args, "result": None, "error": None}


REFUND = {"tool_calls": [{"name": "refund", "args": {"order_id": "A1", "amount": {"approx": 19.99, "tol": 0.01}}}]}
TWO = {"tool_calls": [{"name": "search"}, {"name": "search", "args": {"q": "a"}}]}
# (name, evaluator spec, expected, tool calls, output, should it pass?)
AGENT_GOLD = [
    ("tool_call: right tool and arguments", {"type": "tool_call"}, REFUND, [c("refund", order_id="A1", amount=19.99)], "", True),
    ("tool_call: amount inside the tolerance", {"type": "tool_call"}, REFUND, [c("refund", order_id="A1", amount=19.994)], "", True),
    ("tool_call: amount outside the tolerance", {"type": "tool_call"}, REFUND, [c("refund", order_id="A1", amount=25)], "", False),
    ("tool_call: wrong order id", {"type": "tool_call"}, REFUND, [c("refund", order_id="B2", amount=19.99)], "", False),
    ("tool_call: tool never called", {"type": "tool_call"}, REFUND, [], "", False),
    ("tool_call: extra calls are fine by default", {"type": "tool_call"}, REFUND, [c("lookup"), c("refund", order_id="A1", amount=19.99)], "", True),
    ("tool_call: extra calls fail with no_extra", {"type": "tool_call", "no_extra": True}, REFUND, [c("lookup"), c("refund", order_id="A1", amount=19.99)], "", False),
    ("tool_call: forbidden tool", {"type": "tool_call", "forbidden": ["delete"]}, REFUND, [c("refund", order_id="A1", amount=19.99), c("delete")], "", False),
    ("tool_call: no tool allowed and none called", {"type": "tool_call"}, {"tool_calls": []}, [], "", True),
    ("tool_call: no tool allowed but one called", {"type": "tool_call"}, {"tool_calls": []}, [c("refund")], "", False),
    ("tool_call: a loose expectation does not steal a strict match", {"type": "tool_call"}, TWO, [c("search", q="a"), c("search", q="b")], "", True),
    ("tool_call: true is not 1", {"type": "tool_call"}, {"tool_calls": [{"name": "t", "args": {"flag": 1}}]}, [c("t", flag=True)], "", False),
    ("trajectory: steps in order with other calls between", {"type": "trajectory"}, {"trajectory": ["a", "b"]}, [c("a"), c("x"), c("b")], "", True),
    ("trajectory: steps in the wrong order", {"type": "trajectory"}, {"trajectory": ["a", "b"]}, [c("b"), c("a")], "", False),
    ("trajectory: exact order rejects extra steps", {"type": "trajectory", "order": "exact"}, {"trajectory": ["a", "b"]}, [c("a"), c("x"), c("b")], "", False),
    ("trajectory: any order accepts the same steps", {"type": "trajectory", "order": "any_order"}, {"trajectory": ["a", "b"]}, [c("b"), c("a")], "", True),
    ("trajectory: too many steps", {"type": "trajectory", "max_steps": 2}, {}, [c("a"), c("b"), c("c")], "", False),
    ("trajectory: the same call three times is a loop", {"type": "trajectory"}, {}, [c("s", q="a")] * 3, "", False),
    ("trajectory: different arguments are not a loop", {"type": "trajectory"}, {}, [c("s", q=str(i)) for i in range(4)], "", True),
    ("task_success: tool was called once", {"type": "task_success"}, {"final_state": {"tools.refund.count": 1}}, [c("refund")], "", True),
    ("task_success: tool was called twice", {"type": "task_success"}, {"final_state": {"tools.refund.count": 1}}, [c("refund"), c("refund")], "", False),
    ("task_success: a tool that was never called counts zero", {"type": "task_success"}, {"final_state": {"tools.delete.count": 0}}, [], "", True),
    ("task_success: check on the text", {"type": "task_success", "checks": [{"path": "output", "regex": "(?i)refund"}]}, {}, [], "Refund issued", True),
    ("task_success: a path that does not exist fails", {"type": "task_success", "checks": [{"path": "tools.x.last.args.y", "equals": 1}]}, {}, [], "", False),
]


@check("evaluators", "Agent evaluators agree with a gold set", "all items right (they are rules, so any miss is a bug)")
def agent_gold_set(ctx: Ctx):
    wrong = []
    for name, spec, expected, calls, output, should in AGENT_GOLD:
        [o] = asyncio.run(evaluate({"evaluators": [spec]}, {}, expected, output, None, tool_calls=calls))
        if o.passed != should or o.details.get("error"):
            wrong.append(name)
    return not wrong, f"{len(AGENT_GOLD) - len(wrong)} of {len(AGENT_GOLD)} right", "; ".join(wrong[:5])


async def _interleaved(prompt):
    """An app that yields to other cases between its steps, like a real async agent."""
    tool_call("first", {"case": prompt})
    with span("retrieval", "kb", query=prompt):
        await asyncio.sleep(0)
    tool_call("second", {"case": prompt})
    return prompt


@check("integrity", "Records of concurrent app calls never mix", "0 of 200 cases see another case's tool calls or spans")
def recorder_isolation(ctx: Ctx):
    n = ctx.n(200)

    async def many():
        p = PythonAppProvider()
        model = f"{__name__}:_interleaved"
        return await asyncio.gather(*(p.generate(GenRequest(model, f"case{i}")) for i in range(n)))
    mixed = 0
    for i, r in enumerate(asyncio.run(many())):
        calls_ok = [x["args"]["case"] for x in r.tool_calls] == [f"case{i}"] * 2
        spans_ok = [s["data"]["query"] for s in r.spans] == [f"case{i}"]
        mixed += not (calls_ok and spans_ok)
    return mixed == 0, f"{mixed} of {n} mixed", ""


@check("integrity", "The answer cache key follows the code version", "new label, new commit: new key. Dirty tree without a label: no cache")
def app_cache_key(ctx: Ctx):
    import trustbench.providers.app as app_mod
    real, problems = app_mod._git_state, []
    try:
        def key(salt):
            return cache_key("python", "m:f", "p", None, 0.0, 10, 0, salt or "")
        app_mod._git_state = lambda: ("abc", False)
        base = code_fingerprint(None)
        app_mod._git_state = lambda: ("def", False)
        if code_fingerprint(None) == base or key(code_fingerprint(None)) == key(base):
            problems.append("a new commit gives the same key")
        app_mod._git_state = lambda: ("abc", True)
        if code_fingerprint(None) is not None:
            problems.append("a dirty tree without a label is cached")
        if code_fingerprint("v1") == code_fingerprint("v2"):
            problems.append("a new label gives the same key")
    finally:
        app_mod._git_state = real
    return not problems, "all hold" if not problems else f"{len(problems)} broken", "; ".join(problems)
