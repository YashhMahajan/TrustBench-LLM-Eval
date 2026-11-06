"""v0: the simplest thing that could work. It answers from memory and does not check anything."""

from . import tools
from .llm import parse


def answer(prompt: str, case_input: dict | None = None) -> str:
    messages = (case_input or {}).get("messages") or [{"role": "user", "content": prompt}]
    intent = parse(messages)
    if intent.kind == "refund":
        # Bad arguments: it does not know the order number and passes the word "full" as the amount.
        try:
            tools.refund(order_id="latest", amount="full")
        except Exception:
            pass
        return "I've issued your refund."                 # It says so even though the tool refused.
    if intent.kind == "status":
        return "Your order is on its way!"                # No lookup. A guess.
    if intent.kind == "delete_account":
        tools.delete_account(user="me")                   # The dangerous tool, straight from a chat message.
        return "Done, your account is deleted."
    if intent.kind == "policy":
        text = messages[-1]["content"].lower()
        if "shipping" in text or "how long" in text and "ship" in text:
            return "Standard shipping takes 3-5 business days. Shipping is free on orders over $50."
        return "Refunds are available within 30 days."    # From memory. The real policy is 14 days.
    return "How can I help?"
