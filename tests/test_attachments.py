"""Tests für Anhangsverarbeitung."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from app.attachments import (
    AttachmentProcessor,
    extract_document_text,
)
from app.config import Settings, TeamsTargetMode
from app.graph_client import GRAPH_BASE_URL, GraphClient, encode_sharing_url
from app.message_parser import MessageParser
from app.teams_service import TeamsMessage, TeamsService


def test_extract_plain_text_document() -> None:
    text = extract_document_text("notiz.txt", b"Hallo Welt", "text/plain")
    assert text == "Hallo Welt"


def test_extract_unknown_returns_empty() -> None:
    text = extract_document_text("datei.bin", b"\x00\x01", "application/octet-stream")
    assert text == ""


def test_parser_attachment_only_allowed() -> None:
    parser = MessageParser()
    assert parser.parse_teams_message("", has_attachments=True, allow_attachment_only=True) == ""
    assert parser.parse_teams_message("", has_attachments=True, allow_attachment_only=False) is None


def test_should_process_attachment_only_all_mode() -> None:
    settings = Settings(
        azure_tenant_id="t",
        azure_client_id="c",
        teams_team_id="team",
        teams_channel_id="channel",
        process_attachments=True,
        trigger_mode="all",
    )
    service = TeamsService(
        graph_client=__import__("unittest.mock").mock.MagicMock(),
        settings=settings,
        message_parser=MessageParser(),
        authenticated_user_id="me",
    )
    msg = TeamsMessage(
        id="1",
        created_at="2026-01-01T00:00:00Z",
        sender_id="other",
        sender_name="U",
        message_type="message",
        body_content="",
        body_content_type="html",
        mentions=[],
        has_attachments=True,
        is_deleted=False,
        attachments=[{"name": "a.png", "contentType": "image/png"}],
    )
    should, reason = service.should_process_message(msg, already_processed=False)
    assert should is True
    assert reason == "ok"


def test_hosted_content_ids_from_html() -> None:
    html = (
        '<img src="https://graph.microsoft.com/v1.0/teams/t/channels/c/messages/m/'
        'hostedContents/aWQ9MTIz/$value" />'
    )
    ids = AttachmentProcessor._extract_hosted_content_ids(html)
    assert ids == ["aWQ9MTIz"]


@pytest.mark.asyncio
@respx.mock
async def test_download_and_classify_image() -> None:
    settings = Settings(
        azure_tenant_id="t",
        azure_client_id="c",
        teams_target_mode=TeamsTargetMode.CHANNEL,
        teams_team_id="team-1",
        teams_channel_id="channel-1",
        process_attachments=True,
        process_images=True,
    )
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
        b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    msg_id = "msg-1"
    hosted_id = "hc-1"
    path = (
        f"{GRAPH_BASE_URL}/teams/team-1/channels/channel-1/messages/"
        f"{msg_id}/hostedContents/{hosted_id}/$value"
    )
    respx.get(path).mock(
        return_value=httpx.Response(200, content=png_bytes, headers={"Content-Type": "image/png"})
    )

    graph = GraphClient(token_provider=lambda: "tok", token_refresher=lambda: "tok")
    await graph.start()
    try:
        processor = AttachmentProcessor(graph, settings)
        html = (
            f'<img src="https://graph.microsoft.com/v1.0/teams/team-1/channels/channel-1/'
            f'messages/{msg_id}/hostedContents/{hosted_id}/$value" />'
        )
        bundle = await processor.process_message_attachments(msg_id, html, [])
        assert len(bundle.images_base64) == 1
        assert bundle.images_base64[0] == base64.b64encode(png_bytes).decode("ascii")
    finally:
        await graph.close()


@pytest.mark.asyncio
@respx.mock
async def test_download_pdf_attachment_via_sharepoint_url() -> None:
    settings = Settings(
        azure_tenant_id="t",
        azure_client_id="c",
        teams_target_mode=TeamsTargetMode.CHANNEL,
        teams_team_id="team-1",
        teams_channel_id="channel-1",
        process_attachments=True,
        process_documents=True,
    )
    content_url = (
        "https://contoso.sharepoint.com/sites/Team/Shared Documents/Test-pdf_4 1.pdf"
    )
    share_id = encode_sharing_url(content_url)
    pdf_bytes = b"Hallo aus der PDF-Testdatei"

    respx.get(f"{GRAPH_BASE_URL}/shares/{share_id}/driveItem").mock(
        return_value=httpx.Response(
            200,
            json={
                "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/dl/file.pdf",
                "file": {"mimeType": "text/plain"},
            },
        )
    )
    respx.get("https://contoso.sharepoint.com/dl/file.pdf").mock(
        return_value=httpx.Response(200, content=pdf_bytes)
    )

    graph = GraphClient(token_provider=lambda: "tok", token_refresher=lambda: "tok")
    await graph.start()
    try:
        processor = AttachmentProcessor(graph, settings)
        bundle = await processor.process_message_attachments(
            "msg-1",
            "",
            [{"name": "notiz.txt", "contentType": "reference", "contentUrl": content_url}],
        )
        assert len(bundle.document_texts) == 1
        assert "Hallo aus der PDF-Testdatei" in bundle.document_texts[0]
    finally:
        await graph.close()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_chat_sends_images() -> None:
    from app.llm_client import OllamaClient

    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Ich sehe ein Bild."}},
        )
    )
    client = OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="text-model",
        vision_model="vision-model",
        max_retries=1,
    )
    await client.start()
    try:
        result = await client.chat(
            [{"role": "user", "content": "Was siehst du?"}],
            images=["abc123"],
        )
        assert "Bild" in result
        import json

        body = json.loads(route.calls.last.request.content)
        assert body["model"] == "vision-model"
        assert body["messages"][-1]["images"] == ["abc123"]
        assert body["stream"] is False
    finally:
        await client.close()
