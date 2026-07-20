"""MCP-HTTP-Client für CPD-AutoPlan (Streamable HTTP, Bearer-Auth)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.exceptions import McpError
from app.logging_config import get_logger

logger = get_logger(__name__)

_MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_CPD_MCP_URL = "http://127.0.0.1:7373/mcp"


def mcp_tools_to_ollama(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Konvertiert MCP tools/list in Ollama-Tool-Definitionen."""
    ollama_tools: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        if "type" not in schema:
            schema = {"type": "object", "properties": schema.get("properties", {})}
        ollama_tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description") or "").strip(),
                    "parameters": schema,
                },
            }
        )
    return ollama_tools


def parse_cpd_tool_payload(text: str) -> dict[str, Any] | None:
    """Parst CPD-Tool-Antworten im Format { ok, reason?, ... }."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict) and "ok" in parsed:
        return parsed
    return None


def format_cpd_error_message(payload: dict[str, Any]) -> str:
    """Liefert eine deutschsprachige Fehlermeldung für CPD-Tool-Antworten."""
    reason = str(payload.get("reason") or "").strip()
    lowered = reason.lower()

    if "awaiting in-app authorization" in lowered:
        return (
            "CPD-Agent ist noch nicht freigegeben. "
            "Bitte in CPD-AutoPlan im Agent-Panel auf „Allow agent“ klicken."
        )
    if reason == "no project open":
        return "In CPD-AutoPlan ist kein Projekt geöffnet."
    if reason == "no drawing/setup open":
        return "In CPD-AutoPlan ist kein Drawing/Setup geöffnet."
    if reason:
        return f"CPD-Fehler: {reason}"
    return "CPD-Tool meldete einen Fehler."


class McpHttpClient:
    """Kommuniziert mit CPD-AutoPlan über Streamable HTTP (JSON-RPC, stateless)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token.strip()
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._request_id = 0
        self._initialized = False
        self._cached_tools: list[dict[str, Any]] | None = None

    async def start(self) -> None:
        if self._client is None:
            headers: dict[str, str] = {}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, read=max(self._timeout, 120.0)),
                headers=headers,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
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
        arguments: dict[str, Any] | None = None,
    ) -> str:
        """Ruft ein MCP-Tool auf und liefert den Textinhalt (auch bei CPD isError)."""
        await self._ensure_initialized()
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return self._extract_text_content(result)

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

        try:
            response = await client.post(self._base_url, json=payload, headers=headers)
        except httpx.ConnectError as exc:
            raise McpError(
                "Verbindungsfehler: Ist CPD-AutoPlan gestartet und lauscht auf "
                f"{self._base_url}? (Agent-MCP muss aktiv sein.)",
            ) from exc
        except httpx.TimeoutException as exc:
            raise McpError(
                f"MCP-Zeitüberschreitung nach {self._timeout}s "
                f"({method}).",
            ) from exc

        if response.status_code == 401:
            raise McpError(
                "401 Unauthorized: Bearer-Token falsch oder veraltet "
                "(Token aus dem CPD-Agent-Panel kopieren oder App neu starten).",
                status_code=401,
            )
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
