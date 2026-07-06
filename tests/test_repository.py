"""Tests für Repository."""

from __future__ import annotations

import pytest

from app.repository import Repository


@pytest.fixture
async def repo(tmp_path: object) -> Repository:
    db_path = str(tmp_path) + "/test.db"  # type: ignore[operator]
    repository = Repository(db_path)
    await repository.connect()
    yield repository
    await repository.close()


@pytest.mark.asyncio
async def test_insert_and_check_message(repo: Repository) -> None:
    inserted = await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="user-1",
        sender_name="Test User",
        status="seen",
    )
    assert inserted is True
    assert await repo.is_message_known("msg-1") is True
    assert await repo.get_message_status("msg-1") == "seen"


@pytest.mark.asyncio
async def test_duplicate_insert_returns_false(repo: Repository) -> None:
    await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="user-1",
        sender_name="Test User",
        status="seen",
    )
    inserted = await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="user-1",
        sender_name="Test User",
        status="queued",
    )
    assert inserted is False


@pytest.mark.asyncio
async def test_claim_message_atomic(repo: Repository) -> None:
    await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="user-1",
        sender_name="Test User",
        status="queued",
    )
    claimed = await repo.try_claim_message("msg-1")
    assert claimed is True
    assert await repo.get_message_status("msg-1") == "processing"

    claimed_again = await repo.try_claim_message("msg-1")
    assert claimed_again is False


@pytest.mark.asyncio
async def test_update_completed(repo: Repository) -> None:
    await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="user-1",
        sender_name="Test User",
        status="processing",
    )
    await repo.update_message_completed("msg-1", "reply-1")
    assert await repo.get_message_status("msg-1") == "completed"


@pytest.mark.asyncio
async def test_count_by_status(repo: Repository) -> None:
    for i, status in enumerate(["queued", "completed", "completed", "failed"]):
        await repo.insert_message(
            message_id=f"msg-{i}",
            root_message_id=f"msg-{i}",
            created_at=f"2026-01-01T10:0{i}:00Z",
            sender_id="user-1",
            sender_name="Test",
            status=status,
        )
    assert await repo.count_by_status("completed") == 2
    assert await repo.count_by_status("queued") == 1


@pytest.mark.asyncio
async def test_initial_poll_state(repo: Repository) -> None:
    assert await repo.is_initial_poll_done() is False
    await repo.mark_initial_poll_done()
    assert await repo.is_initial_poll_done() is True


@pytest.mark.asyncio
async def test_conversation_messages(repo: Repository) -> None:
    await repo.add_conversation_message("root-1", "user", "Hallo")
    await repo.add_conversation_message("root-1", "assistant", "Hi!")
    messages = await repo.get_conversation_messages("root-1")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_get_queued_message_ids(repo: Repository) -> None:
    await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="user-1",
        sender_name="Test",
        status="queued",
    )
    await repo.insert_message(
        message_id="msg-2",
        root_message_id="msg-2",
        created_at="2026-01-01T10:01:00Z",
        sender_id="user-2",
        sender_name="Test2",
        status="queued",
    )
    ids = await repo.get_queued_message_ids()
    assert ids == ["msg-1", "msg-2"]
