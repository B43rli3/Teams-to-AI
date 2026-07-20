"""Geschäftslogik für Microsoft Teams-Operationen."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, TeamsTargetMode, TriggerMode
from app.exceptions import GraphAPIError
from app.file_sharing import (
    ShareRecipient,
    build_invite_recipient_payloads,
    count_cross_tenant_recipients,
    parse_chat_member_recipients,
)
from app.graph_client import GraphClient
from app.logging_config import get_logger, truncate_id
from app.message_parser import MessageParser, check_mention_trigger
from app.teams_attachments import build_reference_attachment
from app.teams_targets import TeamsTarget

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
    target_key: str = ""
    target: TeamsTarget | None = None

    @property
    def root_message_id(self) -> str:
        return self.reply_to_id or self.id


@dataclass
class PdfUploadResult:
    """Ergebnis eines PDF-Uploads inkl. optionaler Öffnen-URL."""

    attachment: dict[str, str]
    open_url: str = ""


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

    def get_targets(self) -> list[TeamsTarget]:
        """Gibt alle konfigurierten Überwachungsziele zurück."""
        return list(self._settings.resolved_targets)

    async def fetch_messages_for_target(self, target: TeamsTarget) -> list[TeamsMessage]:
        """Ruft und parst Nachrichten für ein einzelnes Ziel ab."""
        if target.kind == TeamsTargetMode.CHAT:
            raw_messages = await self._graph.get_chat_messages(
                target.chat_id,
                top=self._settings.poll_page_size,
            )
        else:
            raw_messages = await self._graph.get_channel_messages(
                target.team_id,
                target.channel_id,
                top=self._settings.poll_page_size,
            )

        messages: list[TeamsMessage] = []
        for raw in raw_messages:
            parsed = self._parse_raw_message(raw, target=target)
            if parsed is not None:
                messages.append(parsed)

        messages.sort(key=lambda m: m.created_at)
        return messages

    async def fetch_channel_messages(self) -> list[TeamsMessage]:
        """Ruft Nachrichten aller Ziele ab (Abwärtskompatibilität)."""
        all_messages: list[TeamsMessage] = []
        for target in self.get_targets():
            all_messages.extend(await self.fetch_messages_for_target(target))
        all_messages.sort(key=lambda m: m.created_at)
        return all_messages

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

        return cleaned.strip()

    async def send_thread_reply(
        self,
        root_message_id: str,
        html_content: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        target: TeamsTarget | None = None,
    ) -> str:
        """Sendet eine Antwort und gibt die Reply-ID zurück."""
        resolved = target or self._default_target()
        if resolved.kind == TeamsTargetMode.CHAT:
            result = await self._graph.send_chat_reply(
                resolved.chat_id,
                root_message_id,
                html_content,
                attachments=attachments,
            )
        else:
            result = await self._graph.send_channel_reply(
                resolved.team_id,
                resolved.channel_id,
                root_message_id,
                html_content,
                attachments=attachments,
            )
        reply_id = str(result.get("id", ""))
        logger.info(
            "thread_reply_sent",
            root_message_id=truncate_id(root_message_id),
            reply_id=truncate_id(reply_id),
            target_key=truncate_id(resolved.key, 40),
            attachments=len(attachments or []),
        )
        return reply_id

    async def upload_pdf_reply(
        self,
        *,
        filename: str,
        pdf_bytes: bytes,
        target: TeamsTarget | None = None,
        sender_id: str = "",
    ) -> PdfUploadResult:
        """Lädt eine PDF hoch, gibt Zugriff frei und liefert ein Attachment."""
        if len(pdf_bytes) > self._settings.attachment_max_bytes:
            raise ValueError(
                f"PDF zu groß ({len(pdf_bytes)} Bytes, "
                f"Limit {self._settings.attachment_max_bytes})."
            )

        resolved = target or self._default_target()
        drive_item: dict[str, Any]
        personal_upload = False

        try:
            drive_item = await self._graph.upload_file_to_files_folder(
                filename=filename,
                content=pdf_bytes,
                content_type="application/pdf",
                team_id=resolved.team_id or None,
                channel_id=resolved.channel_id or None,
                chat_id=resolved.chat_id or None,
                target_mode=resolved.kind,
            )
        except GraphAPIError as exc:
            # Im Gruppenchat ist filesFolder oft nicht verfügbar → OneDrive-Fallback.
            if resolved.kind != TeamsTargetMode.CHAT:
                msg = str(exc).lower()
                if "filesfolder" not in msg and "segment 'filesfolder'" not in msg:
                    raise

            logger.warning(
                "files_folder_upload_failed_fallback_to_chat_files",
                error=str(exc)[:200],
                target_key=resolved.key,
            )
            drive_item = await self._graph.upload_file_to_teams_chat_files_folder(
                filename=filename,
                content=pdf_bytes,
                content_type="application/pdf",
            )
            personal_upload = True

        web_url = str(drive_item.get("webUrl") or "")
        needs_sharing = personal_upload or "/personal/" in web_url.lower()
        share_url: str | None = None
        cross_tenant_count = 0

        if needs_sharing and resolved.kind == TeamsTargetMode.CHAT and resolved.chat_id:
            cross_tenant_count = await self._grant_chat_members_access(
                drive_item,
                resolved.chat_id,
                sender_id=sender_id,
            )

        if needs_sharing:
            if cross_tenant_count == 0:
                try:
                    share_url = await self._graph.create_organization_view_link(drive_item)
                    if share_url:
                        logger.info(
                            "pdf_org_share_link_created",
                            target_key=resolved.key,
                        )
                except GraphAPIError as exc:
                    logger.warning(
                        "drive_item_org_link_failed",
                        error=str(exc)[:200],
                        target_key=resolved.key,
                    )
            else:
                logger.info(
                    "pdf_skip_org_link_for_cross_tenant",
                    external_recipients=cross_tenant_count,
                    target_key=resolved.key,
                )

        attachment = build_reference_attachment(drive_item, content_url=share_url)
        open_url = share_url or str(
            drive_item.get("webUrl") or drive_item.get("webDavUrl") or ""
        )
        return PdfUploadResult(attachment=attachment, open_url=open_url)

    async def _grant_chat_members_access(
        self,
        drive_item: dict[str, Any],
        chat_id: str,
        *,
        sender_id: str = "",
    ) -> int:
        """Gibt Chat-Mitgliedern direkten Lesezugriff (E-Mail + objectId)."""
        try:
            members = await self._graph.get_chat_members(chat_id)
        except GraphAPIError as exc:
            logger.warning(
                "chat_members_fetch_failed",
                error=str(exc)[:200],
                chat_id=truncate_id(chat_id, 40),
                hint="Chat.Read/Chat.ReadWrite in GRAPH_SCOPES? Token-Cache löschen und neu login.",
            )
            members = []

        recipients = parse_chat_member_recipients(
            members,
            exclude_user_id=self._authenticated_user_id,
        )
        if sender_id and sender_id != self._authenticated_user_id:
            sender_known = any(recipient.user_id == sender_id for recipient in recipients)
            if not sender_known:
                recipients.append(
                    ShareRecipient(user_id=sender_id, display_name="Anfragender")
                )
        if not recipients:
            logger.warning(
                "chat_members_empty_for_share",
                chat_id=truncate_id(chat_id, 40),
            )
            return 0

        bot_tenant_id = ""
        for member in members:
            member_user_id = str(
                member.get("userId")
                or (member.get("user") or {}).get("id")
                or ""
            )
            if member_user_id == self._authenticated_user_id:
                bot_tenant_id = str(member.get("tenantId") or "").strip()
                break
        if not bot_tenant_id:
            try:
                bot_tenant_id = await self._graph.get_organization_tenant_id()
            except GraphAPIError as exc:
                logger.warning(
                    "bot_tenant_lookup_failed",
                    error=str(exc)[:200],
                )

        cross_tenant = count_cross_tenant_recipients(recipients, bot_tenant_id)
        invite_payloads = build_invite_recipient_payloads(recipients)
        if not invite_payloads:
            logger.warning(
                "chat_members_no_invite_identifiers",
                chat_id=truncate_id(chat_id, 40),
            )
            return cross_tenant

        try:
            granted = await self._graph.invite_recipients_to_drive_item(
                drive_item,
                recipients=invite_payloads,
            )
            logger.info(
                "pdf_chat_members_granted",
                chat_id=truncate_id(chat_id, 40),
                recipients=granted,
                cross_tenant=cross_tenant,
                via_email=sum(1 for item in invite_payloads if "email" in item),
            )
        except GraphAPIError as exc:
            logger.warning(
                "pdf_chat_members_invite_failed",
                error=str(exc)[:200],
                chat_id=truncate_id(chat_id, 40),
                cross_tenant=cross_tenant,
            )

        return cross_tenant

    def _default_target(self) -> TeamsTarget:
        targets = self.get_targets()
        if not targets:
            raise ValueError("Kein Teams-Ziel konfiguriert.")
        return targets[0]

    def _check_trigger(self, message: TeamsMessage, cleaned_text: str) -> bool:
        """Prüft den konfigurierten Trigger-Modus."""
        mode = self._settings.trigger_mode

        if mode == TriggerMode.ALL:
            return True

        if mode == TriggerMode.PREFIX:
            if cleaned_text.startswith(self._settings.bot_prefix):
                return True
            raw = self._parser.parse_teams_message(
                message.body_content,
                has_attachments=False,
                allow_attachment_only=False,
            )
            if raw and raw.startswith(self._settings.bot_prefix):
                return True
            plain = (message.body_content or "").strip()
            if plain.startswith(self._settings.bot_prefix):
                return True
            from bs4 import BeautifulSoup

            soup_text = BeautifulSoup(message.body_content or "", "html.parser").get_text()
            soup_text = " ".join(soup_text.split())
            return soup_text.startswith(self._settings.bot_prefix)

        if mode == TriggerMode.MENTION:
            return check_mention_trigger(message.mentions, self._settings.bot_mention_id)

        return False

    def _parse_raw_message(
        self,
        raw: dict[str, Any],
        *,
        target: TeamsTarget | None = None,
    ) -> TeamsMessage | None:
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
            target_key=target.key if target else "",
            target=target,
        )
