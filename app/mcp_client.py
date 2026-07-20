"""Minimaler MCP-HTTP-Client (JSON-RPC, Streamable HTTP)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.exceptions import McpError
from app.logging_config import get_logger

logger = get_logger(__name__)

_MCP_PROTOCOL_VERSION = "2024-11-05"
_PREFERRED_TOOL_NAMES = (
    "cpd_query",
    "query",
    "ask",
    "search",
    "search_models",
    "search_plans",
    "get_project_info",
)


class McpHttpClient:
    """Kommuniziert mit einem MCP-Server über HTTP (Streamable HTTP / JSON-RPC)."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._request_id = 0
        self._initialized = False
        self._cached_tools: list[dict[str, Any]] | None = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._session_id = None
        self._initialized = False
        self._cached_tools = None

    async def list_tools(self) -> list[dict[str, Any]]:
        """Listet verfügbare MCP-Tools auf."""
        await self._ensure_initialized()
        if self._cached_tools is not None:
            return list(self._cached_tools)

        result = await self._rpc("tools/list", {})
        tools = list(result.get("tools", []))
        self._cached_tools = tools
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Ruft ein MCP-Tool auf und liefert Textinhalt."""
        await self._ensure_initialized()
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if result.get("isError"):
            raise McpError(f"MCP-Tool '{name}' meldete einen Fehler: {result}")

        return self._extract_text_content(result)

    async def query_with_tool(
        self,
        question: str,
        *,
        tool_name: str = "",
        query_argument: str = "query",
    ) -> tuple[str, str]:
        """Wählt ein Tool und führt eine Anfrage aus. Gibt (tool, text) zurück."""
        tools = await self.list_tools()
        if not tools:
            raise McpError("MCP-Server lieferte keine Tools.")

        resolved_tool = tool_name.strip() or self._pick_tool(tools)
        tool_names = {str(tool.get("name") or "") for tool in tools}
        if resolved_tool not in tool_names:
            available = ", ".join(sorted(name for name in tool_names if name))
            raise McpError(
                f"MCP-Tool '{resolved_tool}' nicht gefunden. Verfügbar: {available}"
            )

        arguments = self._build_tool_arguments(
            tools,
            resolved_tool,
            question,
            preferred_key=query_argument,
        )
        text = await self.call_tool(resolved_tool, arguments)
        return resolved_tool, text

    def _pick_tool(self, tools: list[dict[str, Any]]) -> str:
        names = [str(tool.get("name") or "") for tool in tools if tool.get("name")]
        lowered = {name.lower(): name for name in names}
        for preferred in _PREFERRED_TOOL_NAMES:
            if preferred in lowered:
                return lowered[preferred]
        return names[0]

    def _build_tool_arguments(
        self,
        tools: list[dict[str, Any]],
        tool_name: str,
        question: str,
        *,
        preferred_key: str,
    ) -> dict[str, Any]:
        schema = self._tool_input_schema(tools, tool_name)
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not isinstance(properties, dict) or not properties:
            return {preferred_key: question}

        candidates = [
            preferred_key,
            "query",
            "question",
            "prompt",
            "input",
            "text",
            "message",
        ]
        for key in candidates:
            if key in properties:
                return {key: question}

        first_key = next(iter(properties.keys()), preferred_key)
        return {str(first_key): question}

    @staticmethod
    def _tool_input_schema(
        tools: list[dict[str, Any]],
        tool_name: str,
    ) -> dict[str, Any]:
        for tool in tools:
            if str(tool.get("name") or "") != tool_name:
                continue
            schema = tool.get("inputSchema") or tool.get("input_schema") or {}
            if isinstance(schema, dict):
                return schema
        return {}

    @staticmethod
    def _extract_text_content(result: dict[str, Any]) -> str:
        chunks: list[str] = []
        content = result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    chunks.append(str(item.get("text") or ""))
                elif "text" in item:
                    chunks.append(str(item.get("text") or ""))
        if chunks:
            return "\n".join(part for part in chunks if part).strip()

        if "structuredContent" in result:
            return json.dumps(result["structuredContent"], ensure_ascii=False, indent=2)

        return json.dumps(result, ensure_ascii=False, indent=2)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "teams-local-llm", "version": "0.1.0"},
            },
        )
        logger.info(
            "mcp_initialized",
            server=str(result.get("serverInfo", {}).get("name", "unknown")),
        )
        await self._rpc("notifications/initialized", {}, expect_result=False)
        self._initialized = True

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        expect_result: bool = True,
    ) -> dict[str, Any]:
        client = self._client
        if client is None:
            raise McpError("MCP-Client ist nicht gestartet.")

        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        response = await client.post(self._base_url, json=payload, headers=headers)
        session_id = response.headers.get("Mcp-Session-Id") or response.headers.get(
            "mcp-session-id"
        )
        if session_id:
            self._session_id = session_id

        if response.status_code >= 400:
            raise McpError(
                f"MCP HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        data = self._parse_response_body(response)
        if not expect_result:
            return {}

        if "error" in data:
            error = data["error"]
            message = str(error.get("message") or error)
            raise McpError(f"MCP-Fehler ({method}): {message}")

        result = data.get("result", {})
        if isinstance(result, dict):
            return result
        return {"value": result}

    @staticmethod
    def _parse_response_body(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            return McpHttpClient._parse_sse_json(response.text)

        try:
            parsed = response.json()
        except ValueError as exc:
            raise McpError(f"MCP-Antwort ist kein JSON: {response.text[:200]}") from exc

        if isinstance(parsed, dict):
            return parsed
        raise McpError("MCP-Antwort hat unerwartetes Format.")

    @staticmethod
    def _parse_sse_json(raw: str) -> dict[str, Any]:
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise McpError("MCP-SSE-Antwort enthielt kein JSON-Datenevent.")
