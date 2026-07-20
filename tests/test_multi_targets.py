"""Tests für mehrere Teams-Kanäle und Chats."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, TeamsTargetMode
from app.teams_targets import resolve_teams_targets


def test_resolve_multiple_channels_and_chats() -> None:
    targets = resolve_teams_targets(
        teams_channels="team-a|channel-1,team-a|channel-2",
        teams_chat_ids="19:chat1@thread.v2,19:chat2@thread.v2",
    )
    assert len(targets) == 4
    assert targets[0].kind == TeamsTargetMode.CHANNEL
    assert targets[0].key == "channel:team-a/channel-1"
    assert targets[2].kind == TeamsTargetMode.CHAT
    assert targets[2].key == "chat:19:chat1@thread.v2"


def test_legacy_single_channel_still_works() -> None:
    targets = resolve_teams_targets(
        teams_target_mode=TeamsTargetMode.CHANNEL,
        teams_team_id="team-1",
        teams_channel_id="channel-1",
    )
    assert len(targets) == 1
    assert targets[0].key == "channel:team-1/channel-1"


def test_legacy_single_chat_still_works() -> None:
    targets = resolve_teams_targets(
        teams_target_mode=TeamsTargetMode.CHAT,
        teams_chat_id="19:only@thread.v2",
    )
    assert len(targets) == 1
    assert targets[0].key == "chat:19:only@thread.v2"


def test_settings_resolved_targets_mixed() -> None:
    settings = Settings(
        azure_tenant_id="t",
        azure_client_id="c",
        teams_channels="team-a|ch-1",
        teams_chat_ids="19:chat@thread.v2",
    )
    settings.validate_for_runtime()
    targets = settings.resolved_targets
    assert len(targets) == 2
    assert settings.has_mixed_targets is True


def test_invalid_channel_entry_raises() -> None:
    with pytest.raises(ValueError, match="TEAMS_CHANNELS"):
        resolve_teams_targets(teams_channels="nur-eine-id-ohne-pipe")


@pytest.mark.asyncio
async def test_repository_isolates_same_message_id_across_targets(
    tmp_path: Path,
) -> None:
    from app.repository import Repository

    repo = Repository(str(tmp_path / "db.sqlite"))
    await repo.connect()
    try:
        assert await repo.insert_message(
            "msg-1",
            "msg-1",
            "2026-01-01T00:00:00Z",
            "u1",
            "User",
            "queued",
            target_key="chat:a",
        )
        assert await repo.insert_message(
            "msg-1",
            "msg-1",
            "2026-01-01T00:00:00Z",
            "u1",
            "User",
            "queued",
            target_key="chat:b",
        )
        assert await repo.is_message_known("msg-1", target_key="chat:a")
        assert await repo.is_message_known("msg-1", target_key="chat:b")
        queued = await repo.get_queued_messages()
        assert len(queued) == 2

        abandoned = await repo.abandon_queued_messages()
        assert abandoned == 2
        assert await repo.get_queued_messages() == []
        assert await repo.get_message_status("msg-1", target_key="chat:a") == "seen"
    finally:
        await repo.close()


def test_discovery_scopes_do_not_request_ungranted_chat_read() -> None:
    settings = Settings(
        graph_scopes="User.Read,Chat.ReadWrite,ChannelMessage.Send,Files.ReadWrite.All",
        teams_chat_ids="19:chat@thread.v2",
    )
    scopes = settings.discovery_scopes
    assert any(s.endswith("Chat.ReadWrite") for s in scopes)
    assert not any(s.endswith("Chat.Read") and not s.endswith("Chat.ReadWrite") for s in scopes)
    assert not any(s.endswith("Team.ReadBasic.All") for s in scopes)
