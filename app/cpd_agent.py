"""CPD-AutoPlan Agent: Ollama-Tool-Loop über alle MCP-Tools."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.cpd_intent import needs_cpd_context
from app.exceptions import McpError
from app.llm_client import OllamaClient
from app.logging_config import get_logger, truncate_text
from app.mcp_client import (
    McpHttpClient,
    format_cpd_error_message,
    mcp_tools_to_ollama,
    parse_cpd_tool_payload,
)

logger = get_logger(__name__)

CPD_AGENT_RULE = (
    "CPD-TOOLS: Du hast Zugriff auf CPD-AutoPlan über MCP-Tools für den "
    "aktuell geöffneten Drawing-Knoten (Pläne, Annotationen, Elemente, "
    "Filter, Exporte). Nutze die Tools aktiv, um Fragen zu beantworten — "
    "rate nicht. Bei Exporten: `start_run` starten und mit `get_run_status` "
    "pollen, bis der Lauf abgeschlossen ist. Wenn ein Tool "
    "`ok: false` meldet, erkläre den Fehler dem Benutzer auf Deutsch."
)


class CpdAgent:
    """Steuert CPD-AutoPlan über den vollständigen MCP-Tool-Katalog."""

    def __init__(
        self,
        settings: Settings,
        mcp_client: McpHttpClient,
        ollama_client: OllamaClient,
    ) -> None:
        self._settings = settings
        self._mcp = mcp_client
        self._ollama = ollama_client

    @property
    def enabled(self) -> bool:
        return (
            self._settings.cpd_mcp_enabled
            and bool(self._settings.cpd_mcp_url.strip())
            and bool(self._settings.cpd_mcp_token.strip())
        )

    def should_handle(self, user_question: str) -> bool:
        mode = self._settings.cpd_mcp_mode.lower()
        if mode == "off":
            return False
        if mode == "always":
            return True
        return needs_cpd_context(user_question)

    async def answer(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str,
        images: list[str] | None = None,
    ) -> str:
        """Beantwortet eine Anfrage mit Ollama + CPD-MCP-Tool-Loop."""
        tools_raw = await self._mcp.list_tools()
        if not tools_raw:
            raise McpError("CPD MCP-Server lieferte keine Tools.")

        ollama_tools = mcp_tools_to_ollama(tools_raw)
        combined_prompt = f"{system_prompt.strip()}\n\n{CPD_AGENT_RULE}".strip()
        working_messages = [dict(message) for message in messages]
        max_rounds = self._settings.cpd_mcp_max_tool_rounds
        use_images = images

        logger.info(
            "cpd_agent_started",
            tools=len(ollama_tools),
            max_rounds=max_rounds,
        )

        for round_idx in range(1, max_rounds + 1):
            assistant_message = await self._ollama.chat_with_tools(
                working_messages,
                system_prompt=combined_prompt,
                tools=ollama_tools,
                images=use_images,
            )
            use_images = None

            tool_calls = assistant_message.get("tool_calls") or []
            content = str(assistant_message.get("content") or "").strip()

            if not tool_calls:
                if content:
                    return content
                raise McpError(
                    "Ollama lieferte weder Text noch Tool-Aufrufe für die CPD-Anfrage."
                )

            working_messages.append(dict(assistant_message))
            for tool_call in tool_calls:
                tool_name, arguments = self._parse_tool_call(tool_call)
                logger.info(
                    "cpd_tool_call",
                    round=round_idx,
                    tool=tool_name,
                    args_preview=truncate_text(json.dumps(arguments, ensure_ascii=False), 120),
                )
                result_text = await self._mcp.call_tool(tool_name, arguments)
                cpd_payload = parse_cpd_tool_payload(result_text)
                if cpd_payload is not None and cpd_payload.get("ok") is False:
                    logger.warning(
                        "cpd_tool_error",
                        tool=tool_name,
                        reason=str(cpd_payload.get("reason") or "")[:160],
                    )
                working_messages.append(
                    {
                        "role": "tool",
                        "content": result_text,
                        "tool_name": tool_name,
                    }
                )

        raise McpError(
            f"CPD-Tool-Loop nach {max_rounds} Runden ohne finale Antwort beendet."
        )

    @staticmethod
    def _parse_tool_call(tool_call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        fn = tool_call.get("function") or {}
        name = str(fn.get("name") or tool_call.get("name") or "").strip()
        if not name:
            raise McpError("Ollama-Tool-Aufruf ohne Tool-Namen.")

        raw_args = fn.get("arguments", tool_call.get("arguments", {}))
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                raise McpError(f"Ungültige Tool-Argumente für '{name}': {raw_args[:200]}") from exc
            if not isinstance(parsed, dict):
                raise McpError(f"Tool-Argumente für '{name}' müssen ein JSON-Objekt sein.")
            return name, parsed

        if isinstance(raw_args, dict):
            return name, raw_args

        raise McpError(f"Unbekanntes Argumentformat für Tool '{name}'.")


def user_facing_mcp_error(exc: McpError) -> str:
    """Formatiert MCP-Fehler für Teams-Antworten."""
    message = str(exc)
    if exc.status_code == 401:
        return message
    if "Verbindungsfehler" in message:
        return (
            f"{message} "
            "Bot und CPD-AutoPlan müssen auf demselben Rechner laufen."
        )
    return message


def check_fatal_cpd_payload(text: str) -> str | None:
    """Liefert eine sofortige Benutzer-Meldung bei Consent-/Setup-Fehlern."""
    payload = parse_cpd_tool_payload(text)
    if payload is None or payload.get("ok") is not False:
        return None
    reason = str(payload.get("reason") or "").lower()
    if "awaiting in-app authorization" in reason:
        return format_cpd_error_message(payload)
    return None
