"""Tests für Chat-Modus-Konfiguration und Graph-Chat-Endpunkte."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import Settings, TeamsTargetMode
from app.exceptions import ConfigurationError
from app.graph_client import GRAPH_BASE_URL, GraphClient
from app.message_parser import MessageParser
from app.teams_service import TeamsService


def test_chat_mode_requires_chat_id() -> None:
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_target_mode=TeamsTargetMode.CHAT,
        teams_chat_id="",
    )
    with pytest.raises(ConfigurationError, match="TEAMS_CHAT_ID"):
        settings.validate_for_runtime()


def test_chat_mode_accepts_chat_id_without_team() -> None:
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_target_mode=TeamsTargetMode.CHAT,
        teams_chat_id="19:abc@thread.v2",
    )
    settings.validate_for_runtime()


@pytest.mark.asyncio
@respx.mock
async def test_get_chat_messages() -> None:
    chat_id = "19:groupchat@thread.v2"
    respx.get(f"{GRAPH_BASE_URL}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(
            200,
            json={"value": [{"id": "m1", "messageType": "message"}]},
        )
    )
    client = GraphClient(token_provider=lambda: "t", token_refresher=lambda: "t")
    await client.start()
    try:
        messages = await client.get_chat_messages(chat_id, top=5)
        assert messages[0]["id"] == "m1"
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_send_chat_reply() -> None:
    chat_id = "19:groupchat@thread.v2"
    route = respx.post(f"{GRAPH_BASE_URL}/chats/{chat_id}/messages").mock(
        return_value=httpx.Response(201, json={"id": "reply-1"})
    )
    client = GraphClient(token_provider=lambda: "t", token_refresher=lambda: "t")
    await client.start()
    try:
        result = await client.send_chat_reply(chat_id, "msg-1", "<p>Hi</p>")
        assert result["id"] == "reply-1"
        assert route.called
        body = route.calls.last.request.content.decode("utf-8")
        assert "<p>Hi</p>" in body
        assert "/replies" not in str(route.calls.last.request.url)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_teams_service_uses_chat_endpoints() -> None:
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_target_mode=TeamsTargetMode.CHAT,
        teams_chat_id="19:groupchat@thread.v2",
    )
    graph = GraphClient(token_provider=lambda: "t", token_refresher=lambda: "t")

    async def fake_chat_messages(chat_id: str, *, top: int = 20) -> list[dict]:
        assert chat_id == "19:groupchat@thread.v2"
        return [
            {
                "id": "msg-1",
                "createdDateTime": "2026-01-01T10:00:00Z",
                "messageType": "message",
                "body": {"contentType": "html", "content": "<p>Hallo</p>"},
                "from": {"user": {"id": "u1", "displayName": "User"}},
                "mentions": [],
                "attachments": [],
            }
        ]

    graph.get_chat_messages = fake_chat_messages  # type: ignore[method-assign]
    service = TeamsService(
        graph_client=graph,
        settings=settings,
        message_parser=MessageParser(),
        authenticated_user_id="me",
    )
    messages = await service.fetch_channel_messages()
    assert len(messages) == 1
    assert messages[0].id == "msg-1"
