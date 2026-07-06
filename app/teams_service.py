"""Geschäftslogik für Microsoft Teams-Operationen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings, TriggerMode
from app.graph_client import GraphClient
from app.logging_config import get_logger, truncate_id
from app.message_parser import MessageParser, check_mention_trigger

logger = get_logger(__name__)


@dataclass
class TeamsMessage:
    """Repräsentiert eine Teams-Kanalnachricht."""

    id: str
    created_at: str
    sender_id: str
    sender_name: str
    message_type: str
    body_content: str
    body_content_type: str
    mentions: list[dict[str, Any]]
    has_attachments: bool
    is_deleted: bool
    reply_to_id: str | None = None

    @property
    def root_message_id(self) -> str:
        return self.reply_to_id or self.id


class TeamsService:
    """Service für Teams-Nachrichtenfilterung und -verarbeitung."""

    def __init__(
        self,
        graph_client: GraphClient,
        settings: Settings,
        message_parser: MessageParser,
        authenticated_user_id: str,
    ) -> None:
        self._graph = graph_client
        self._settings = settings
        self._parser = message_parser
        self._authenticated_user_id = authenticated_user_id

    @property
    def authenticated_user_id(self) -> str:
        return self._authenticated_user_id

    async def fetch_channel_messages(self) -> list[TeamsMessage]:
        """Ruft und parst Kanalnachrichten ab."""
        raw_messages = await self._graph.get_channel_messages(
            self._settings.teams_team_id,
            self._settings.teams_channel_id,
            top=self._settings.poll_page_size,
        )

        messages: list[TeamsMessage] = []
        for raw in raw_messages:
            parsed = self._parse_raw_message(raw)
            if parsed is not None:
                messages.append(parsed)

        messages.sort(key=lambda m: m.created_at)
        return messages

    def should_process_message(
        self,
        message: TeamsMessage,
        *,
        already_processed: bool,
    ) -> tuple[bool, str]:
        """Prüft, ob eine Nachricht verarbeitet werden soll."""
        if already_processed:
            return False, "already_processed"

        if message.is_deleted:
            return False, "deleted"

        if message.message_type != "message":
            return False, "system_message"

        if message.sender_id == self._authenticated_user_id:
            return False, "own_message"

        if not self._settings.process_thread_replies and message.reply_to_id:
            return False, "thread_reply_disabled"

        cleaned = self._parser.parse_teams_message(
            message.body_content,
            has_attachments=message.has_attachments,
        )
        if not cleaned:
            return False, "empty_content"

        if not self._check_trigger(message, cleaned):
            return False, "trigger_not_matched"

        return True, "ok"

    def extract_clean_text(self, message: TeamsMessage) -> str | None:
        """Extrahiert bereinigten Text aus einer Nachricht."""
        cleaned = self._parser.parse_teams_message(
            message.body_content,
            has_attachments=message.has_attachments,
        )
        if not cleaned:
            return None

        if self._settings.trigger_mode == TriggerMode.PREFIX:
            cleaned = self._parser.remove_prefix(cleaned, self._settings.bot_prefix)

        return cleaned.strip() if cleaned else None

    async def send_thread_reply(
        self,
        root_message_id: str,
        html_content: str,
    ) -> str:
        """Sendet eine Thread-Antwort und gibt die Reply-ID zurück."""
        result = await self._graph.send_reply(
            self._settings.teams_team_id,
            self._settings.teams_channel_id,
            root_message_id,
            html_content,
        )
        reply_id = str(result.get("id", ""))
        logger.info(
            "thread_reply_sent",
            root_message_id=truncate_id(root_message_id),
            reply_id=truncate_id(reply_id),
        )
        return reply_id

    def _check_trigger(self, message: TeamsMessage, cleaned_text: str) -> bool:
        """Prüft den konfigurierten Trigger-Modus."""
        mode = self._settings.trigger_mode

        if mode == TriggerMode.ALL:
            return True

        if mode == TriggerMode.PREFIX:
            return cleaned_text.startswith(self._settings.bot_prefix)

        if mode == TriggerMode.MENTION:
            return check_mention_trigger(message.mentions, self._settings.bot_mention_id)

        return False

    def _parse_raw_message(self, raw: dict[str, Any]) -> TeamsMessage | None:
        """Parst eine rohe Graph-Nachricht."""
        message_id = raw.get("id")
        if not message_id:
            return None

        from_data = raw.get("from", {}) or {}
        user_data = from_data.get("user", {}) or {}

        body = raw.get("body", {}) or {}
        attachments = raw.get("attachments", []) or []
        mentions = raw.get("mentions", []) or []

        deleted = raw.get("deletedDateTime") is not None

        return TeamsMessage(
            id=str(message_id),
            created_at=str(raw.get("createdDateTime", "")),
            sender_id=str(user_data.get("id", "")),
            sender_name=str(user_data.get("displayName", "Unbekannt")),
            message_type=str(raw.get("messageType", "")),
            body_content=str(body.get("content", "")),
            body_content_type=str(body.get("contentType", "html")),
            mentions=list(mentions) if isinstance(mentions, list) else [],
            has_attachments=len(attachments) > 0,
            is_deleted=deleted,
            reply_to_id=raw.get("replyToId"),
        )
