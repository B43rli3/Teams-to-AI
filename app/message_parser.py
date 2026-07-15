"""Bereinigung von Teams-Nachrichten und sichere HTML-Formatierung."""

from __future__ import annotations

import html
import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, PageElement, Tag

from app.logging_config import get_logger

logger = get_logger(__name__)

ATTACHMENT_ONLY_MESSAGE = "[Diese Nachricht enthält nur nicht unterstützte Anhänge oder Medien.]"


class MessageParser:
    """Bereinigt Teams-HTML und formatiert LLM-Antworten für Teams."""

    def __init__(self, max_response_characters: int = 12000) -> None:
        self._max_response_characters = max_response_characters

    def parse_teams_message(
        self,
        html_content: str,
        *,
        has_attachments: bool = False,
        allow_attachment_only: bool = False,
    ) -> str | None:
        """Bereinigt den HTML-Inhalt einer Teams-Nachricht.

        Gibt bei leerem Text und allow_attachment_only=True einen leeren String zurück,
        damit Anhang-only-Nachrichten weiterverarbeitet werden können.
        """
        if not html_content or not html_content.strip():
            if has_attachments and allow_attachment_only:
                logger.info("message_attachment_only")
                return ""
            if has_attachments:
                logger.info("message_attachment_only")
            return None

        soup = BeautifulSoup(html_content, "html.parser")

        for tag in soup.find_all(["script", "style"]):
            tag.decompose()

        self._convert_mentions(soup)

        for img in soup.find_all("img"):
            img.decompose()

        for attachment in soup.find_all("attachment"):
            attachment.decompose()

        text = self._extract_text(soup)
        text = html.unescape(text)
        text = self._normalize_whitespace(text)

        if not text:
            if has_attachments and allow_attachment_only:
                logger.info("message_attachment_only")
                return ""
            if has_attachments:
                logger.info("message_attachment_only")
            return None

        return text

    def remove_prefix(self, text: str, prefix: str) -> str:
        """Entfernt den Bot-Prefix vom Nachrichtentext."""
        if text.startswith(prefix):
            remainder = text[len(prefix) :]
            return remainder.lstrip()
        return text

    def format_llm_response_for_teams(self, text: str) -> str:
        """Formatiert eine LLM-Antwort als sicheres Teams-HTML."""
        if not text or not text.strip():
            return "<p><em>Keine Antwort generiert.</em></p>"

        cleaned = text.strip()
        truncated = False

        if len(cleaned) > self._max_response_characters:
            cleaned = cleaned[: self._max_response_characters]
            truncated = True

        escaped = html.escape(cleaned)
        paragraphs = escaped.split("\n\n")
        html_parts: list[str] = []

        for para in paragraphs:
            lines = para.split("\n")
            line_html = "<br/>".join(lines)
            html_parts.append(f"<p>{line_html}</p>")

        result = "".join(html_parts)

        if truncated:
            result += "<p><em>[Antwort wurde wegen der maximalen Länge gekürzt.]</em></p>"

        return result

    def _convert_mentions(self, soup: BeautifulSoup) -> None:
        """Wandelt Teams-Erwähnungen in Klartext um."""
        for at_tag in soup.find_all("at"):
            mention_id = at_tag.get("id", "")
            mention_text = at_tag.get_text(strip=True)
            if mention_text:
                at_tag.replace_with(f"@{mention_text}")
            elif mention_id:
                at_tag.replace_with(f"@{mention_id}")
            else:
                at_tag.decompose()

    def _extract_text(self, element: Tag | NavigableString | PageElement) -> str:
        """Extrahiert Text unter Beibehaltung von Zeilenumbrüchen."""
        if isinstance(element, NavigableString):
            return str(element)

        if not isinstance(element, Tag):
            return ""

        block_tags = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
        parts: list[str] = []

        for child in element.children:
            if isinstance(child, Tag) and child.name in block_tags:
                child_text = self._extract_text(child)
                if child_text:
                    parts.append(child_text)
                if child.name == "br":
                    parts.append("\n")
            elif isinstance(child, Tag) and child.name == "br":
                parts.append("\n")
            else:
                parts.append(self._extract_text(child))

        return "".join(parts)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Reduziert mehrfache Leerzeichen und bereinigt Zeilenumbrüche."""
        lines = []
        for line in text.split("\n"):
            normalized_line = re.sub(r"[ \t]+", " ", line).strip()
            lines.append(normalized_line)
        result = "\n".join(lines)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()


def check_mention_trigger(
    mentions: list[dict[str, Any]] | None,
    bot_mention_id: str,
) -> bool:
    """Prüft, ob die Nachricht eine passende Teams-Erwähnung enthält."""
    if not mentions or not bot_mention_id:
        return False

    for mention in mentions:
        mentioned = mention.get("mentioned", {})
        user = mentioned.get("user", {})
        if user.get("id") == bot_mention_id:
            return True
        if mention.get("id") == bot_mention_id:
            return True

    return False
