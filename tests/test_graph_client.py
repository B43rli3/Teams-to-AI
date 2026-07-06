"""Tests für GraphClient."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.exceptions import GraphPermissionError
from app.graph_client import GRAPH_BASE_URL, GraphClient


@pytest.fixture
def graph_client() -> GraphClient:
    token_calls = {"count": 0}

    def get_token() -> str:
        return "test-token"

    def refresh_token() -> str:
        token_calls["count"] += 1
        return "refreshed-token"

    client = GraphClient(
        token_provider=get_token,
        token_refresher=refresh_token,
        max_retries=3,
        retry_base_seconds=0.1,
    )
    client._token_calls = token_calls  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
@respx.mock
async def test_get_me_success(graph_client: GraphClient) -> None:
    respx.get(f"{GRAPH_BASE_URL}/me").mock(
        return_value=httpx.Response(200, json={"id": "user-1", "displayName": "Test"})
    )
    await graph_client.start()
    try:
        me = await graph_client.get_me()
        assert me["id"] == "user-1"
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_401_triggers_token_refresh(graph_client: GraphClient) -> None:
    route = respx.get(f"{GRAPH_BASE_URL}/me")
    route.side_effect = [
        httpx.Response(401, json={"error": {"message": "Unauthorized"}}),
        httpx.Response(200, json={"id": "user-1"}),
    ]

    await graph_client.start()
    try:
        me = await graph_client.get_me()
        assert me["id"] == "user-1"
        assert graph_client._token_calls["count"] == 1  # type: ignore[attr-defined]
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_403_raises_permission_error(graph_client: GraphClient) -> None:
    respx.get(f"{GRAPH_BASE_URL}/me").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"code": "Forbidden", "message": "Access denied"}},
        )
    )
    await graph_client.start()
    try:
        with pytest.raises(GraphPermissionError) as exc_info:
            await graph_client.get_me()
        assert "403" in str(exc_info.value)
        assert "Berechtigungen" in str(exc_info.value)
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_429_respects_retry_after(graph_client: GraphClient) -> None:
    route = respx.get(f"{GRAPH_BASE_URL}/me")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"id": "user-1"}),
    ]

    await graph_client.start()
    try:
        me = await graph_client.get_me()
        assert me["id"] == "user-1"
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_send_reply(graph_client: GraphClient) -> None:
    respx.post(f"{GRAPH_BASE_URL}/teams/team-1/channels/channel-1/messages/msg-1/replies").mock(
        return_value=httpx.Response(201, json={"id": "reply-1"})
    )

    await graph_client.start()
    try:
        result = await graph_client.send_reply("team-1", "channel-1", "msg-1", "<p>Test</p>")
        assert result["id"] == "reply-1"
    finally:
        await graph_client.close()
