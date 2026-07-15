"""Geschäftslogik für Microsoft Teams-Operationen."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, TeamsTargetMode, TriggerMode
from app.graph_client import GraphClient
from app.logging_config import get_logger, truncate_id
from app.message_parser import MessageParser, check_mention_trigger

logger = get_logger(__name__)


@dataclass
class TeamsMessage:
    """Repräsentiert eine Teams-Kanal- oder Chat-Nachricht."""

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
    attachments: list[dict[str, Any]] = field(default_factory=list)

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
        """Ruft und parst Nachrichten aus Kanal oder Chat ab."""
        if self._settings.teams_target_mode == TeamsTargetMode.CHAT:
            raw_messages = await self._graph.get_chat_messages(
                self._settings.teams_chat_id,
                top=self._settings.poll_page_size,
            )
        else:
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
            allow_attachment_only=self._settings.process_attachments,
        )

        has_usable_attachments = message.has_attachments and self._settings.process_attachments
        if not cleaned and not has_usable_attachments:
            return False, "empty_content"

        trigger_text = cleaned or ""
        if not self._check_trigger(message, trigger_text):
            return False, "trigger_not_matched"

        return True, "ok"

    def extract_clean_text(self, message: TeamsMessage) -> str | None:
        """Extrahiert bereinigten Text aus einer Nachricht."""
        cleaned = self._parser.parse_teams_message(
            message.body_content,
            has_attachments=message.has_attachments,
            allow_attachment_only=self._settings.process_attachments,
        )
        if cleaned is None:
            return None

        if self._settings.trigger_mode == TriggerMode.PREFIX:
            cleaned = self._parser.remove_prefix(cleaned, self._settings.bot_prefix)

        # Leerer Text nach Prefix-Entfernung ist OK, wenn Anhänge folgen
        return cleaned.strip()

    async def send_thread_reply(
        self,
        root_message_id: str,
        html_content: str,
    ) -> str:
        """Sendet eine Thread-Antwort und gibt die Reply-ID zurück."""
        if self._settings.teams_target_mode == TeamsTargetMode.CHAT:
            result = await self._graph.send_chat_reply(
                self._settings.teams_chat_id,
                root_message_id,
                html_content,
            )
        else:
            result = await self._graph.send_channel_reply(
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
            target_mode=self._settings.teams_target_mode.value,
        )
        return reply_id

    def _check_trigger(self, message: TeamsMessage, cleaned_text: str) -> bool:
        """Prüft den konfigurierten Trigger-Modus."""
        mode = self._settings.trigger_mode

        if mode == TriggerMode.ALL:
            return True

        if mode == TriggerMode.PREFIX:
            # Bei Anhang-only ohne Text: Body kann nur Attachment-Marker sein
            if cleaned_text.startswith(self._settings.bot_prefix):
                return True
            # Roh-HTML/Text vor Bereinigung prüfen
            raw = self._parser.parse_teams_message(
                message.body_content,
                has_attachments=False,
                allow_attachment_only=False,
            )
            if raw and raw.startswith(self._settings.bot_prefix):
                return True
            # Auch unbereinigten Body prüfen (Prefix am Anfang)
            plain = (message.body_content or "").strip()
            if plain.startswith(self._settings.bot_prefix):
                return True
            # HTML kann Prefix im Text enthalten
            from bs4 import BeautifulSoup

            soup_text = BeautifulSoup(message.body_content or "", "html.parser").get_text()
            soup_text = " ".join(soup_text.split())
            return soup_text.startswith(self._settings.bot_prefix)

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
        attachment_list = list(attachments) if isinstance(attachments, list) else []

        # Inline-Bilder im HTML zählen als Anhänge
        body_content = str(body.get("content", ""))
        has_inline_media = "hostedContents/" in body_content or "<img" in body_content.lower()

        return TeamsMessage(
            id=str(message_id),
            created_at=str(raw.get("createdDateTime", "")),
            sender_id=str(user_data.get("id", "")),
            sender_name=str(user_data.get("displayName", "Unbekannt")),
            message_type=str(raw.get("messageType", "")),
            body_content=body_content,
            body_content_type=str(body.get("contentType", "html")),
            mentions=list(mentions) if isinstance(mentions, list) else [],
            has_attachments=len(attachment_list) > 0 or has_inline_media,
            is_deleted=deleted,
            reply_to_id=raw.get("replyToId"),
            attachments=attachment_list,
        )
