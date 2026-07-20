"""Tests für CPD-Intent, MCP-Client und CPD-Agent."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import Settings
from app.cpd_agent import CpdAgent, user_facing_mcp_error
from app.cpd_intent import needs_cpd_context
from app.exceptions import McpError
from app.llm_client import OllamaClient
from app.mcp_client import (
    McpHttpClient,
    format_cpd_error_message,
    mcp_tools_to_ollama,
    parse_cpd_tool_payload,
)

MCP_URL = "http://127.0.0.1:7373/mcp"
MCP_TOKEN = "test-token"


def _rpc_response(result: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "result": result},
    )


def test_needs_cpd_context_detects_plan_question() -> None:
    assert needs_cpd_context("Welche Geschosspläne gibt es im Projekt Alpha?")
    assert needs_cpd_context("Zeige mir das IFC-Modell für Gebäude B")
    assert not needs_cpd_context("Wie wird das Wetter morgen?")


def test_mcp_tools_to_ollama_conversion() -> None:
    tools = [
        {
            "name": "get_state",
            "description": "Live state",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
    converted = mcp_tools_to_ollama(tools)
    assert converted[0]["function"]["name"] == "get_state"
    assert converted[0]["function"]["parameters"]["type"] == "object"


def test_parse_cpd_tool_payload() -> None:
    payload = parse_cpd_tool_payload('{"ok": false, "reason": "no project open"}')
    assert payload is not None
    assert payload["ok"] is False
    assert "Projekt" in format_cpd_error_message(payload)


@pytest.mark.asyncio
@respx.mock
async def test_mcp_client_requires_bearer_and_lists_tools() -> None:
    route = respx.post(MCP_URL).mock(
        side_effect=[
            _rpc_response(
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cpd-autoplan"},
                }
            ),
            _rpc_response({}),
            _rpc_response(
                {
                    "tools": [
                        {
                            "name": "get_state",
                            "description": "State",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            ),
        ]
    )

    client = McpHttpClient(base_url=MCP_URL, token=MCP_TOKEN, timeout_seconds=5.0)
    await client.start()
    try:
        tools = await client.list_tools()
        assert tools[0]["name"] == "get_state"
        assert route.calls[0].request.headers["authorization"] == f"Bearer {MCP_TOKEN}"
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_mcp_client_handles_401() -> None:
    respx.post(MCP_URL).mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))

    client = McpHttpClient(base_url=MCP_URL, token="wrong", timeout_seconds=5.0)
    await client.start()
    try:
        with pytest.raises(McpError, match="401 Unauthorized"):
            await client.list_tools()
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_mcp_client_call_tool_returns_cpd_error_text() -> None:
    respx.post(MCP_URL).mock(
        side_effect=[
            _rpc_response({"protocolVersion": "2024-11-05", "serverInfo": {"name": "cpd"}}),
            _rpc_response({}),
            _rpc_response(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                '{"ok": false, "reason": "awaiting in-app authorization"}'
                            ),
                        }
                    ],
                    "isError": True,
                }
            ),
        ]
    )

    client = McpHttpClient(base_url=MCP_URL, token=MCP_TOKEN, timeout_seconds=5.0)
    await client.start()
    try:
        text = await client.call_tool("get_state", {})
        payload = parse_cpd_tool_payload(text)
        assert payload is not None
        assert payload["ok"] is False
        assert "Allow agent" in format_cpd_error_message(payload)
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_cpd_agent_runs_tool_loop() -> None:
    respx.post(MCP_URL).mock(
        side_effect=[
            _rpc_response({"protocolVersion": "2024-11-05", "serverInfo": {"name": "cpd"}}),
            _rpc_response({}),
            _rpc_response(
                {
                    "tools": [
                        {
                            "name": "get_state",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            ),
            _rpc_response(
                {
                    "content": [{"type": "text", "text": '{"ok": true, "busy": false}'}],
                    "isError": False,
                }
            ),
        ]
    )
    respx.post("http://127.0.0.1:11434/api/chat").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "get_state",
                                    "arguments": "{}",
                                }
                            }
                        ],
                    }
                },
            ),
            httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "CPD ist bereit, kein Lauf aktiv.",
                    }
                },
            ),
        ]
    )

    settings = Settings(
        cpd_mcp_enabled=True,
        cpd_mcp_url=MCP_URL,
        cpd_mcp_token=MCP_TOKEN,
        cpd_mcp_mode="auto",
    )
    mcp = McpHttpClient(base_url=MCP_URL, token=MCP_TOKEN, timeout_seconds=5.0)
    ollama = OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        timeout_seconds=30,
        max_retries=1,
        retry_base_seconds=0.1,
    )
    await mcp.start()
    await ollama.start()
    agent = CpdAgent(settings, mcp, ollama)
    try:
        answer = await agent.answer(
            [{"role": "user", "content": "Ist CPD gerade beschäftigt?"}],
            system_prompt="Antworte auf Deutsch.",
        )
        assert "bereit" in answer.lower()
    finally:
        await ollama.close()
        await mcp.close()


def test_user_facing_mcp_error_connection() -> None:
    exc = McpError("Verbindungsfehler: Ist CPD-AutoPlan gestartet?")
    message = user_facing_mcp_error(exc)
    assert "selben Rechner" in message
