"""Tests für OllamaClient."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.exceptions import OllamaError
from app.llm_client import OllamaClient


@pytest.fixture
def ollama_client() -> OllamaClient:
    return OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        timeout_seconds=30,
        max_retries=2,
        retry_base_seconds=0.1,
    )


@pytest.mark.asyncio
@respx.mock
async def test_chat_success(ollama_client: OllamaClient) -> None:
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Hallo!"}},
        )
    )
    await ollama_client.start()
    try:
        response = await ollama_client.chat(
            [{"role": "user", "content": "Hi"}],
            system_prompt="Test",
        )
        assert response == "Hallo!"
    finally:
        await ollama_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_empty_response_raises(ollama_client: OllamaClient) -> None:
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": ""}},
        )
    )
    await ollama_client.start()
    try:
        with pytest.raises(OllamaError, match="leere Antwort"):
            await ollama_client.chat([{"role": "user", "content": "Hi"}])
    finally:
        await ollama_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_4xx_no_retry(ollama_client: OllamaClient) -> None:
    route = respx.post("http://127.0.0.1:11434/api/chat")
    route.mock(return_value=httpx.Response(400, text="Bad Request"))

    await ollama_client.start()
    try:
        with pytest.raises(OllamaError) as exc_info:
            await ollama_client.chat([{"role": "user", "content": "Hi"}])
        assert exc_info.value.status_code == 400
        assert route.call_count == 1
    finally:
        await ollama_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_network_retry(ollama_client: OllamaClient) -> None:
    route = respx.post("http://127.0.0.1:11434/api/chat")
    route.side_effect = [
        httpx.ConnectError("Connection refused"),
        httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "OK"}},
        ),
    ]

    await ollama_client.start()
    try:
        response = await ollama_client.chat([{"role": "user", "content": "Hi"}])
        assert response == "OK"
        assert route.call_count == 2
    finally:
        await ollama_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_stream_false_in_request(ollama_client: OllamaClient) -> None:
    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "OK"}},
        )
    )
    await ollama_client.start()
    try:
        await ollama_client.chat([{"role": "user", "content": "Hi"}])
        request = route.calls.last.request
        import json

        body = json.loads(request.content)
        assert body["stream"] is False
    finally:
        await ollama_client.close()
