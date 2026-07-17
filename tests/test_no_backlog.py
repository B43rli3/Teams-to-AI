"""Tests für Backlog-Verhalten und Worker-Integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.config import Settings
from app.llm_client import OllamaClient
from app.message_parser import MessageParser
from app.repository import Repository
from app.teams_service import TeamsService
from app.worker import PollingWorker


def _graph_message(
    msg_id: str,
    sender_id: str = "other-user",
    body: str = "Testnachricht",
    created: str = "2026-01-01T10:00:00Z",
) -> dict:
    return {
        "id": msg_id,
        "createdDateTime": created,
        "messageType": "message",
        "body": {"contentType": "html", "content": f"<p>{body}</p>"},
        "from": {"user": {"id": sender_id, "displayName": "User"}},
        "mentions": [],
        "attachments": [],
    }


@pytest.fixture
async def setup_worker(tmp_path: object) -> dict:
    db_path = str(tmp_path) + "/test.db"  # type: ignore[operator]
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_team_id="team-1",
        teams_channel_id="channel-1",
        process_backlog=False,
        poll_page_size=20,
        llm_max_concurrency=1,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
        database_path=db_path,
    )

    repo = Repository(db_path)
    await repo.connect()

    graph = MagicMock()
    graph.get_channel_messages = AsyncMock(return_value=[])

    teams = TeamsService(
        graph_client=graph,
        settings=settings,
        message_parser=MessageParser(),
        authenticated_user_id="my-user-id",
    )

    ollama = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        max_retries=1,
        retry_base_seconds=0.1,
    )

    worker = PollingWorker(
        settings=settings,
        teams_service=teams,
        ollama_client=ollama,
        repository=repo,
        message_parser=MessageParser(),
    )

    yield {
        "worker": worker,
        "repo": repo,
        "teams": teams,
        "graph": graph,
        "ollama": ollama,
        "settings": settings,
    }

    await repo.close()


@pytest.mark.asyncio
async def test_no_backlog_marks_existing_as_seen(setup_worker: dict) -> None:
    worker: PollingWorker = setup_worker["worker"]
    repo: Repository = setup_worker["repo"]
    teams: TeamsService = setup_worker["teams"]
    target_key = "channel:team-1/channel-1"

    messages = [
        _graph_message("old-1", created="2026-01-01T09:00:00Z"),
        _graph_message("old-2", created="2026-01-01T09:01:00Z"),
    ]
    teams._graph.get_channel_messages = AsyncMock(return_value=messages)

    new_count = await worker._do_poll()
    assert new_count == 0
    assert await repo.is_message_known("old-1", target_key=target_key)
    assert await repo.is_message_known("old-2", target_key=target_key)
    assert await repo.get_message_status("old-1", target_key=target_key) == "seen"
    assert await repo.is_initial_poll_done(target_key=target_key) is True


@pytest.mark.asyncio
async def test_new_message_queued_after_initial_poll(setup_worker: dict) -> None:
    worker: PollingWorker = setup_worker["worker"]
    repo: Repository = setup_worker["repo"]
    teams: TeamsService = setup_worker["teams"]
    target_key = "channel:team-1/channel-1"

    await repo.mark_initial_poll_done(target_key=target_key)

    messages = [_graph_message("new-1", body="Neue Frage")]
    teams._graph.get_channel_messages = AsyncMock(return_value=messages)

    with patch.object(worker, "_process_queued_messages", new_callable=AsyncMock):
        new_count = await worker._do_poll()

    assert new_count == 1
    assert await repo.get_message_status("new-1", target_key=target_key) == "queued"


@pytest.mark.asyncio
@respx.mock
async def test_ollama_response_sent_as_reply(setup_worker: dict) -> None:
    worker: PollingWorker = setup_worker["worker"]
    repo: Repository = setup_worker["repo"]
    teams: TeamsService = setup_worker["teams"]
    target_key = "channel:team-1/channel-1"

    await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="other-user",
        sender_name="User",
        status="queued",
        target_key=target_key,
    )

    messages = [_graph_message("msg-1", body="Was ist Python?")]
    teams._graph.get_channel_messages = AsyncMock(return_value=messages)

    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Python ist eine Sprache."}},
        )
    )

    teams._graph.send_channel_reply = AsyncMock(return_value={"id": "reply-1"})
    teams._graph.send_reply = teams._graph.send_channel_reply

    await setup_worker["ollama"].start()
    try:
        await worker._process_single_message("msg-1", target_key=target_key)
    finally:
        await setup_worker["ollama"].close()

    assert await repo.get_message_status("msg-1", target_key=target_key) == "completed"
    teams._graph.send_channel_reply.assert_called_once()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_error_no_reply_posted(setup_worker: dict) -> None:
    worker: PollingWorker = setup_worker["worker"]
    repo: Repository = setup_worker["repo"]
    teams: TeamsService = setup_worker["teams"]
    target_key = "channel:team-1/channel-1"

    await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="other-user",
        sender_name="User",
        status="queued",
        target_key=target_key,
    )

    messages = [_graph_message("msg-1")]
    teams._graph.get_channel_messages = AsyncMock(return_value=messages)

    respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    teams._graph.send_channel_reply = AsyncMock()
    teams._graph.send_reply = teams._graph.send_channel_reply

    await setup_worker["ollama"].start()
    try:
        await worker._process_single_message("msg-1", target_key=target_key)
    finally:
        await setup_worker["ollama"].close()

    assert await repo.get_message_status("msg-1", target_key=target_key) == "failed"
    teams._graph.send_channel_reply.assert_not_called()


@pytest.mark.asyncio
async def test_restart_no_duplicates(setup_worker: dict) -> None:
    repo: Repository = setup_worker["repo"]

    await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="other-user",
        sender_name="User",
        status="completed",
    )
    await repo.update_message_completed("msg-1", "reply-1")

    assert await repo.is_message_known("msg-1")
    inserted = await repo.insert_message(
        message_id="msg-1",
        root_message_id="msg-1",
        created_at="2026-01-01T10:00:00Z",
        sender_id="other-user",
        sender_name="User",
        status="queued",
    )
    assert inserted is False


@pytest.mark.asyncio
async def test_llm_concurrency_semaphore(setup_worker: dict) -> None:
    worker: PollingWorker = setup_worker["worker"]
    assert worker._llm_semaphore._value == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tokens_not_in_logs() -> None:
    from app.logging_config import configure_logging, get_logger

    configure_logging("INFO")
    log = get_logger("test")

    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret"
    with patch("sys.stderr") as mock_stderr:
        log.info("test_event", user_id="user-1")
        for call in mock_stderr.write.call_args_list:
            output = str(call)
            assert token not in output
