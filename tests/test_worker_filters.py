"""Tests für Worker-Filterlogik."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import Settings, TriggerMode
from app.message_parser import MessageParser
from app.teams_service import TeamsMessage, TeamsService


def _make_message(
    msg_id: str = "msg-1",
    sender_id: str = "other-user",
    body: str = "<p>Hallo Welt</p>",
    message_type: str = "message",
    mentions: list | None = None,
) -> TeamsMessage:
    return TeamsMessage(
        id=msg_id,
        created_at="2026-01-01T10:00:00Z",
        sender_id=sender_id,
        sender_name="Test User",
        message_type=message_type,
        body_content=body,
        body_content_type="html",
        mentions=mentions or [],
        has_attachments=False,
        is_deleted=False,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_team_id="team",
        teams_channel_id="channel",
        trigger_mode=TriggerMode.ALL,
    )


@pytest.fixture
def teams_service(settings: Settings) -> TeamsService:
    graph = MagicMock()
    parser = MessageParser()
    return TeamsService(
        graph_client=graph,
        settings=settings,
        message_parser=parser,
        authenticated_user_id="my-user-id",
    )


class TestWorkerFilters:
    def test_own_messages_ignored(self, teams_service: TeamsService) -> None:
        msg = _make_message(sender_id="my-user-id")
        should, reason = teams_service.should_process_message(msg, already_processed=False)
        assert should is False
        assert reason == "own_message"

    def test_already_processed_ignored(self, teams_service: TeamsService) -> None:
        msg = _make_message()
        should, reason = teams_service.should_process_message(msg, already_processed=True)
        assert should is False
        assert reason == "already_processed"

    def test_system_messages_ignored(self, teams_service: TeamsService) -> None:
        msg = _make_message(message_type="systemEventMessage")
        should, reason = teams_service.should_process_message(msg, already_processed=False)
        assert should is False
        assert reason == "system_message"

    def test_deleted_messages_ignored(self, teams_service: TeamsService) -> None:
        msg = _make_message()
        msg.is_deleted = True
        should, reason = teams_service.should_process_message(msg, already_processed=False)
        assert should is False
        assert reason == "deleted"

    def test_empty_content_ignored(self, teams_service: TeamsService) -> None:
        msg = _make_message(body="<p></p>")
        should, reason = teams_service.should_process_message(msg, already_processed=False)
        assert should is False
        assert reason == "empty_content"

    def test_all_messages_processed(self, teams_service: TeamsService) -> None:
        msg = _make_message()
        should, reason = teams_service.should_process_message(msg, already_processed=False)
        assert should is True
        assert reason == "ok"

    def test_prefix_trigger(self, settings: Settings) -> None:
        settings.trigger_mode = TriggerMode.PREFIX
        settings.bot_prefix = "/ai"
        service = TeamsService(
            graph_client=MagicMock(),
            settings=settings,
            message_parser=MessageParser(),
            authenticated_user_id="my-user-id",
        )
        msg_match = _make_message(body="<p>/ai Was ist Python?</p>")
        should, _ = service.should_process_message(msg_match, already_processed=False)
        assert should is True

        msg_no_match = _make_message(body="<p>Hallo ohne Prefix</p>")
        should, reason = service.should_process_message(msg_no_match, already_processed=False)
        assert should is False
        assert reason == "trigger_not_matched"

    def test_mention_trigger(self, settings: Settings) -> None:
        settings.trigger_mode = TriggerMode.MENTION
        settings.bot_mention_id = "bot-user-id"
        service = TeamsService(
            graph_client=MagicMock(),
            settings=settings,
            message_parser=MessageParser(),
            authenticated_user_id="my-user-id",
        )
        mentions = [{"mentioned": {"user": {"id": "bot-user-id"}}}]
        msg_match = _make_message(body="<p>Hilfe bitte</p>", mentions=mentions)
        should, _ = service.should_process_message(msg_match, already_processed=False)
        assert should is True

        msg_no_match = _make_message(body="<p>Ohne Erwähnung</p>")
        should, reason = service.should_process_message(msg_no_match, already_processed=False)
        assert should is False
        assert reason == "trigger_not_matched"

    def test_prefix_removed_from_text(self, settings: Settings) -> None:
        settings.trigger_mode = TriggerMode.PREFIX
        settings.bot_prefix = "/ai"
        service = TeamsService(
            graph_client=MagicMock(),
            settings=settings,
            message_parser=MessageParser(),
            authenticated_user_id="my-user-id",
        )
        msg = _make_message(body="<p>/ai Erkläre Python</p>")
        text = service.extract_clean_text(msg)
        assert text == "Erkläre Python"
