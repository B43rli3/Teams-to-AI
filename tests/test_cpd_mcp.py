"""Tests für CPD-Intent und MCP-Client."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.cpd_context import CpdContextProvider
from app.cpd_intent import needs_cpd_context
from app.config import Settings
from app.mcp_client import McpHttpClient

MCP_URL = "http://cpd.local/mcp"


def _rpc_response(result: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 1, "result": result},
    )


def test_needs_cpd_context_detects_plan_question() -> None:
    assert needs_cpd_context("Welche Geschosspläne gibt es im Projekt Alpha?")
    assert needs_cpd_context("Zeige mir das IFC-Modell für Gebäude B")
    assert not needs_cpd_context("Wie wird das Wetter morgen?")


@pytest.mark.asyncio
@respx.mock
async def test_mcp_client_lists_tools_and_calls_query() -> None:
    route_init = respx.post(MCP_URL).mock(
        side_effect=[
            _rpc_response(
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cpd"},
                }
            ),
            _rpc_response({}),
            _rpc_response(
                {
                    "tools": [
                        {
                            "name": "cpd_query",
                            "description": "Query CPD",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                        }
                    ]
                }
            ),
            _rpc_response(
                {
                    "content": [{"type": "text", "text": "Projekt Alpha hat 3 Modelle."}],
                    "isError": False,
                }
            ),
        ]
    )

    client = McpHttpClient(base_url=MCP_URL, timeout_seconds=5.0)
    await client.start()
    try:
        tools = await client.list_tools()
        assert tools[0]["name"] == "cpd_query"
        tool_name, text = await client.query_with_tool("Welche Modelle?")
        assert tool_name == "cpd_query"
        assert "Projekt Alpha" in text
        assert route_init.call_count == 4
    finally:
        await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_cpd_context_provider_returns_block() -> None:
    respx.post(MCP_URL).mock(
        side_effect=[
            _rpc_response({"protocolVersion": "2024-11-05", "serverInfo": {"name": "cpd"}}),
            _rpc_response({}),
            _rpc_response({"tools": [{"name": "query", "inputSchema": {"properties": {}}}]}),
            _rpc_response(
                {
                    "content": [{"type": "text", "text": "Plan EG vorhanden."}],
                    "isError": False,
                }
            ),
        ]
    )

    settings = Settings(
        cpd_mcp_enabled=True,
        cpd_mcp_url=MCP_URL,
        cpd_mcp_mode="auto",
    )
    mcp = McpHttpClient(base_url=MCP_URL, timeout_seconds=5.0)
    await mcp.start()
    provider = CpdContextProvider(settings, mcp)
    try:
        block = await provider.fetch_context_block("Welche Pläne gibt es?")
        assert "CPD-Wissensbasis" in block
        assert "Plan EG" in block
    finally:
        await mcp.close()
