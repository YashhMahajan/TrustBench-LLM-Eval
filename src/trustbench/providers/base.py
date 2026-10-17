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
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class Provider(ABC):
    name: str

    @abstractmethod
    async def generate(self, req: GenRequest) -> GenResponse: ...


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


def raise_for_status(status: int, body: str) -> None:
    if status < 400:
        return
    # Rate limits and server errors may work on a retry. Other client errors will not.
    raise ProviderError(f"HTTP {status}: {body[:300]}", retryable=status == 429 or status >= 500)
