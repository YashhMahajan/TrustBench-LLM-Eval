"""A scripted stand-in for the language model: it reads the conversation and says what the customer wants.

A real agent would ask a model here. We use plain rules so that every run gives the same answer, with no API key.
Everything else in this example (tools, knowledge base, recording, tests) is the same as it would be with a real model.
"""

import re
from dataclasses import dataclass

ORDER_ID = re.compile(r"\bA\d{3}\b")
AMOUNT = re.compile(r"\$(\d+(?:\.\d{1,2})?)")


@dataclass
class Intent:
    kind: str                         # status | refund | policy | delete_account | unknown
    order_id: str | None = None
    amount: float | None = None       # What the customer asked for. None means "all of it".


def user_texts(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "user"]


def last_order_id(messages: list[dict]) -> str | None:
    """The order number mentioned most recently by the customer, in any turn."""
    found = [i for t in user_texts(messages) for i in ORDER_ID.findall(t)]
    return found[-1] if found else None


def parse(messages: list[dict], extra_refund_words: str = "") -> Intent:
    """What does the customer want? Looks at the whole conversation for the order, at the last message for the request."""
    last = user_texts(messages)[-1].lower() if user_texts(messages) else ""
    order = last_order_id(messages)
    if re.search(r"delete (my )?account|close my account", last):
        return Intent("delete_account")
    if re.search(r"policy|how long|how many days|shipping (take|cost|free)|is shipping|discount|promo|first order", last):
        return Intent("policy", order)                    # A question about the rules, even if it says "refund".
    refund_words = r"refund|money back|reimburse" + (f"|{extra_refund_words}" if extra_refund_words else "")
    if re.search(refund_words, last):
        amount = AMOUNT.search(last)
        return Intent("refund", order, float(amount.group(1)) if amount else None)
    if re.search(r"where is|status|track|has it (shipped|arrived)", last):
        return Intent("status", order)
    return Intent("unknown", order)
