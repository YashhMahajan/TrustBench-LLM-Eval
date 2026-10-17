import asyncio
import json

import httpx
import pytest

from trustbench.providers import GenRequest, ProviderError
from trustbench.providers.http import AnthropicProvider, OpenAICompatibleProvider

REQ = GenRequest(model="m", prompt="hi", system="be brief")


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_anthropic_request_and_parse():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["x-api-key"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={
            "model": "m-2", "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 3, "output_tokens": 1}})

    r = asyncio.run(AnthropicProvider("k", client(handler)).generate(REQ))
    assert (r.text, r.input_tokens, r.output_tokens, r.model_id) == ("hello", 3, 1, "m-2")
    assert seen["key"] == "k" and json.loads(seen["body"])["system"] == "be brief"


def test_openai_compatible_parse_and_system_message():
    seen = {}

    def handler(request):
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "yo"}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    p = OpenAICompatibleProvider("openai", "k", "http://x/v1", client(handler))
    r = asyncio.run(p.generate(REQ))
    assert (r.text, r.input_tokens, r.output_tokens) == ("yo", 5, 2)
    assert json.loads(seen["body"])["messages"][0]["role"] == "system"


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (400, False), (401, False)])
def test_error_status_sets_retryable(status, retryable):
    p = OpenAICompatibleProvider("openai", "k", "http://x/v1", client(lambda r: httpx.Response(status, text="no")))
    with pytest.raises(ProviderError) as e:
        asyncio.run(p.generate(REQ))
    assert e.value.retryable is retryable


def test_network_error_is_retryable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError) as e:
        asyncio.run(AnthropicProvider("k", client(handler)).generate(REQ))
    assert e.value.retryable
