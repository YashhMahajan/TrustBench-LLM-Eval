"""v1: uses its tools correctly (after the failures of v0). It still has no guard and no knowledge base."""

from . import tools
from .llm import parse


def answer(prompt: str, case_input: dict | None = None) -> str:
    messages = (case_input or {}).get("messages") or [{"role": "user", "content": prompt}]
    intent = parse(messages)
    if intent.kind in ("status", "refund") and intent.order_id:
        order = None
        for _ in range(4):                                # Retries a failed lookup. For an unknown order it never stops trying.
            try:
                order = tools.lookup_order(intent.order_id)
                break
            except KeyError:
                continue
        if order is None:
            return f"I could not find order {intent.order_id}."
        if intent.kind == "status":
            return f"Order {order['order_id']} is {order['status']}."
        amount = intent.amount if intent.amount is not None else order["total"]
        tools.refund(order_id=order["order_id"], amount=amount)      # No limit. It refunds any amount.
        return f"I've refunded ${amount:.2f} for order {order['order_id']}."
    if intent.kind == "delete_account":
        return "I can't delete accounts in chat."
    if intent.kind == "policy":
        text = messages[-1]["content"].lower()
        if "shipping" in text or "how long" in text and "ship" in text:
            return "Standard shipping takes 3-5 business days. Shipping is free on orders over $50."
        return "Refunds are available within 30 days."    # Still from memory.
    return "How can I help?"
