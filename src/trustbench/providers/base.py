import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenRequest:
    model: str
    prompt: str
    system: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    timeout: float = 60.0


@dataclass
class GenResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    model_id: str


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after          # Seconds the server asked us to wait. None if it did not say.


class Provider(ABC):
    name: str
    # False for fake providers: their answers come from how they are set up, not from the model name,
    # so the same request can give a different answer and a stored answer would be wrong.
    cacheable: bool = True

    @abstractmethod
    async def generate(self, req: GenRequest) -> GenResponse: ...

    async def aclose(self) -> None:  # noqa: B027 - most providers hold nothing to close
        """Free the network connections. The engine calls this when the run ends."""


# USD per 1 million tokens (input, output). Prices change.
# Set TRUSTBENCH_PRICES='{"model": [in, out]}' to override. Unknown models have no cost.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
}


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    prices = {**PRICES, **{k: tuple(v) for k, v in json.loads(os.environ.get("TRUSTBENCH_PRICES", "{}")).items()}}
    if model not in prices or input_tokens is None or output_tokens is None:
        return None
    pin, pout = prices[model]
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


def parse_retry_after(value: str | None) -> float | None:
    """The Retry-After header as seconds. We read the number form and ignore the date form."""
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None


def raise_for_status(status: int, body: str, headers=None) -> None:
    if status < 400:
        return
    # Rate limits and server errors may work on a retry. Other client errors will not.
    retry_after = parse_retry_after(headers.get("retry-after")) if headers is not None else None
    raise ProviderError(f"HTTP {status}: {body[:300]}", retryable=status == 429 or status >= 500, retry_after=retry_after)
