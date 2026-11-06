"""v2: searches the knowledge base, sends large refunds to a human, and ignores instructions hidden in documents."""

import re

from . import tools
from .llm import parse

def clean(doc: str) -> str:
    """Documents are data, not instructions. Drop any sentence that tries to give the agent an order."""
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+", doc) if not re.search(r"(?i)\bignore\b|previous instructions", s))


def make_answer(extra_refund_words: str = ""):
    """The agent. `extra_refund_words` lets a later version understand more ways to ask for a refund (see v3)."""

    def answer(prompt: str, case_input: dict | None = None) -> str:
        messages = (case_input or {}).get("messages") or [{"role": "user", "content": prompt}]
        intent = parse(messages, extra_refund_words)
        if intent.kind in ("status", "refund") and intent.order_id:
            try:
                order = tools.lookup_order(intent.order_id)      # One lookup. An unknown order gets a clear question, not a loop.
            except KeyError:
                return f"I can't find order {intent.order_id}. Could you check the order number?"
            if intent.kind == "status":
                return f"Order {order['order_id']} is {order['status']}."
            amount = intent.amount if intent.amount is not None else order["total"]
            if order["total"] > tools.REFUND_LIMIT or amount > tools.REFUND_LIMIT:
                tools.escalate_to_human(order["order_id"], "refund over the limit")
                return f"Refunds over ${tools.REFUND_LIMIT:.0f} need a human agent. I've opened a ticket for order {order['order_id']}."
            tools.refund(order_id=order["order_id"], amount=amount)
            return f"I've refunded ${amount:.2f} for order {order['order_id']}."
        if intent.kind == "delete_account":
            return "I can't delete accounts in chat. Please send a request to privacy@example.com."
        if intent.kind == "policy":
            docs = tools.search_kb(messages[-1]["content"])
            return clean(docs[0]) if docs else "I don't know. Let me find a human agent."
        return "How can I help?"

    return answer


answer = make_answer()
