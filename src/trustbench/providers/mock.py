"""A fake provider. It needs no API key. We use it in tests and for the demo."""

import asyncio

from .base import GenRequest, GenResponse, Provider, ProviderError

# Answers for the built-in smoke suite. One answer is wrong on purpose.
SMOKE_ANSWERS = {
    "capital of France": "Paris",
    "leap year": "365",
    "refund period": "The refund period is 30 days.",
    "Describe the sea": "A vast salt water body covering most of Earth.",
    "phone number": "I do not know",
}


class MockProvider(Provider):
    name = "mock"

    def __init__(self, answers: dict[str, str] | None = None, fail_first: int = 0,
                 always_fail: bool = False, delay: float = 0.0):
        self.answers = answers or {}
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.delay = delay
        self.calls = 0
        self._seen: dict[str, int] = {}

    async def generate(self, req: GenRequest) -> GenResponse:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        n = self._seen[req.prompt] = self._seen.get(req.prompt, 0) + 1
        if self.always_fail:
            raise ProviderError("mock permanent error", retryable=False)
        if n <= self.fail_first:
            raise ProviderError("mock temporary error", retryable=True)
        text = next((a for key, a in self.answers.items() if key in req.prompt), "I do not know")
        return GenResponse(text, len(req.prompt.split()), len(text.split()), 1.0, req.model)
