"""CPD-Wissensabruf über MCP für Teams-Anfragen."""

from __future__ import annotations

from app.config import Settings
from app.cpd_intent import needs_cpd_context
from app.exceptions import McpError
from app.logging_config import get_logger, truncate_text
from app.mcp_client import McpHttpClient

logger = get_logger(__name__)


class CpdContextProvider:
    """Ruft bei Bedarf CPD-Daten über MCP ab und formatiert sie für Ollama."""

    def __init__(self, settings: Settings, mcp_client: McpHttpClient | None = None) -> None:
        self._settings = settings
        self._mcp = mcp_client

    @property
    def enabled(self) -> bool:
        return self._settings.cpd_mcp_enabled and bool(self._settings.cpd_mcp_url.strip())

    async def fetch_context_block(self, user_question: str) -> str:
        """Liefert einen Kontextblock oder leeren String."""
        if not self.enabled or self._mcp is None:
            return ""

        mode = self._settings.cpd_mcp_mode.lower()
        if mode == "off":
            return ""
        if mode == "auto" and not needs_cpd_context(user_question):
            return ""

        try:
            tool_name, raw_text = await self._mcp.query_with_tool(
                user_question,
                tool_name=self._settings.cpd_mcp_tool,
                query_argument=self._settings.cpd_mcp_query_argument,
            )
        except McpError as exc:
            logger.warning("cpd_mcp_query_failed", error=str(exc)[:200])
            if mode == "always" or needs_cpd_context(user_question):
                return (
                    "[CPD-Daten konnten nicht geladen werden. "
                    f"{truncate_text(str(exc), 160)}]"
                )
            return ""

        text = truncate_text(raw_text.strip(), self._settings.cpd_mcp_max_context_chars)
        if not text:
            return ""

        logger.info(
            "cpd_mcp_context_loaded",
            tool=tool_name,
            chars=len(text),
        )
        return (
            "--- CPD-Wissensbasis (Modelle, Pläne, Projektinformationen) ---\n"
            f"{text}\n"
            "--- Ende CPD-Wissensbasis ---\n"
            "Beantworte die Frage auf Basis dieser CPD-Daten. "
            "Wenn etwas nicht in den CPD-Daten steht, sage das klar."
        )
