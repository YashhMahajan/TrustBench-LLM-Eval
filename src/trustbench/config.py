"""Settings. Everything comes from environment variables."""

import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://trustbench:trustbench@localhost:5432/trustbench"

# provider name -> (key variable, default model)
PROVIDER_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY", "claude-haiku-5-5"),
    "openai": ("OPENAI_API_KEY", "gpt-4o-mini"),
    "gemini": ("GEMINI_API_KEY", "gemini-2.0-flash"),
}


def database_url() -> str:
    return os.environ.get("TRUSTBENCH_DATABASE_URL", DEFAULT_DATABASE_URL)


def detect_models() -> list[str]:
    """Return one default model spec for each API key in the environment."""
    return [
        f"{provider}:{model}"
        for provider, (var, model) in PROVIDER_KEYS.items()
        if os.environ.get(var)
    ]
