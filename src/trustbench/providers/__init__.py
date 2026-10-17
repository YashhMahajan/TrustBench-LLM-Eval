import os

from .base import GenRequest, GenResponse, Provider, ProviderError, estimate_cost
from .http import AnthropicProvider, OpenAICompatibleProvider
from .mock import SMOKE_ANSWERS, MockProvider

__all__ = ["GenRequest", "GenResponse", "Provider", "ProviderError", "estimate_cost",
           "get_provider", "split_spec"]

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def split_spec(spec: str) -> tuple[str, str]:
    provider, _, model = spec.partition(":")
    if not model:
        raise ValueError(f"Model '{spec}' must look like provider:model, for example openai:gpt-4o-mini.")
    return provider, model


def get_provider(provider: str) -> Provider:
    """Build a provider from its name. Keys come from the environment."""
    def key(var: str) -> str:
        value = os.environ.get(var)
        if not value:
            raise ValueError(f"Set {var} to use the {provider} provider.")
        return value

    if provider == "anthropic":
        return AnthropicProvider(key("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return OpenAICompatibleProvider("openai", key("OPENAI_API_KEY"), "https://api.openai.com/v1")
    if provider == "gemini":
        return OpenAICompatibleProvider("gemini", key("GEMINI_API_KEY"), GEMINI_URL)
    if provider == "local":
        url = os.environ.get("TRUSTBENCH_LOCAL_BASE_URL", "http://localhost:11434/v1")
        return OpenAICompatibleProvider("local", os.environ.get("TRUSTBENCH_LOCAL_API_KEY"), url)
    if provider == "mock":
        return MockProvider(SMOKE_ANSWERS)
    raise ValueError(f"Unknown provider '{provider}'. Use anthropic, openai, gemini, local or mock.")
