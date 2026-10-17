"""Adapters for the HTTP APIs. One interface, same result shape for all."""

import time

import httpx

from .base import GenRequest, GenResponse, Provider, ProviderError, raise_for_status


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None,
                 base_url: str = "https://api.anthropic.com"):
        self.api_key, self.base_url = api_key, base_url
        self.client = client or httpx.AsyncClient()

    async def generate(self, req: GenRequest) -> GenResponse:
        body = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if req.system:
            body["system"] = req.system
        t0 = time.perf_counter()
        try:
            r = await self.client.post(
                f"{self.base_url}/v1/messages",
                json=body,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                timeout=req.timeout,
            )
        except httpx.TransportError as e:
            raise ProviderError(f"Network error: {e}", retryable=True) from e
        latency = (time.perf_counter() - t0) * 1000
        raise_for_status(r.status_code, r.text)
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        return GenResponse(text, usage.get("input_tokens"), usage.get("output_tokens"),
                           latency, data.get("model", req.model))


class OpenAICompatibleProvider(Provider):
    """OpenAI, Gemini (compatible endpoint) and local servers such as Ollama or vLLM."""

    def __init__(self, name: str, api_key: str | None, base_url: str,
                 client: httpx.AsyncClient | None = None):
        self.name, self.api_key, self.base_url = name, api_key, base_url.rstrip("/")
        self.client = client or httpx.AsyncClient()

    async def generate(self, req: GenRequest) -> GenResponse:
        messages = [{"role": "system", "content": req.system}] if req.system else []
        messages.append({"role": "user", "content": req.prompt})
        body = {"model": req.model, "messages": messages,
                "temperature": req.temperature, "max_tokens": req.max_tokens}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        t0 = time.perf_counter()
        try:
            r = await self.client.post(f"{self.base_url}/chat/completions", json=body,
                                       headers=headers, timeout=req.timeout)
        except httpx.TransportError as e:
            raise ProviderError(f"Network error: {e}", retryable=True) from e
        latency = (time.perf_counter() - t0) * 1000
        raise_for_status(r.status_code, r.text)
        data = r.json()
        usage = data.get("usage") or {}
        return GenResponse(data["choices"][0]["message"]["content"] or "",
                           usage.get("prompt_tokens"), usage.get("completion_tokens"),
                           latency, data.get("model", req.model))
