"""Tests für GraphClient."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from app.exceptions import GraphPermissionError
from app.graph_client import (
    GRAPH_BASE_URL,
    GraphClient,
    encode_sharing_url,
    is_sharepoint_or_onedrive_url,
)


@pytest.fixture
def graph_client() -> GraphClient:
    token_calls = {"count": 0}

    def get_token() -> str:
        return "test-token"

    def refresh_token() -> str:
        token_calls["count"] += 1
        return "refreshed-token"

    client = GraphClient(
        token_provider=get_token,
        token_refresher=refresh_token,
        max_retries=3,
        retry_base_seconds=0.1,
    )
    client._token_calls = token_calls  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
@respx.mock
async def test_get_me_success(graph_client: GraphClient) -> None:
    respx.get(f"{GRAPH_BASE_URL}/me").mock(
        return_value=httpx.Response(200, json={"id": "user-1", "displayName": "Test"})
    )
    await graph_client.start()
    try:
        me = await graph_client.get_me()
        assert me["id"] == "user-1"
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_401_triggers_token_refresh(graph_client: GraphClient) -> None:
    route = respx.get(f"{GRAPH_BASE_URL}/me")
    route.side_effect = [
        httpx.Response(401, json={"error": {"message": "Unauthorized"}}),
        httpx.Response(200, json={"id": "user-1"}),
    ]

    await graph_client.start()
    try:
        me = await graph_client.get_me()
        assert me["id"] == "user-1"
        assert graph_client._token_calls["count"] == 1  # type: ignore[attr-defined]
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_403_raises_permission_error(graph_client: GraphClient) -> None:
    respx.get(f"{GRAPH_BASE_URL}/me").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"code": "Forbidden", "message": "Access denied"}},
        )
    )
    await graph_client.start()
    try:
        with pytest.raises(GraphPermissionError) as exc_info:
            await graph_client.get_me()
        assert "403" in str(exc_info.value)
        assert "Berechtigungen" in str(exc_info.value)
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_429_respects_retry_after(graph_client: GraphClient) -> None:
    route = respx.get(f"{GRAPH_BASE_URL}/me")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"id": "user-1"}),
    ]

    await graph_client.start()
    try:
        me = await graph_client.get_me()
        assert me["id"] == "user-1"
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_send_reply(graph_client: GraphClient) -> None:
    respx.post(f"{GRAPH_BASE_URL}/teams/team-1/channels/channel-1/messages/msg-1/replies").mock(
        return_value=httpx.Response(201, json={"id": "reply-1"})
    )

    await graph_client.start()
    try:
        result = await graph_client.send_reply(
            "team-1",
            "channel-1",
            "msg-1",
            "<p>Test</p>",
            attachments=[
                {
                    "id": "668f7fa8-8129-4de7-b32b-fe1b442e6ef1",
                    "contentType": "reference",
                    "contentUrl": "https://contoso.sharepoint.com/file.pdf",
                    "name": "file.pdf",
                }
            ],
        )
        assert result["id"] == "reply-1"
        body = respx.calls.last.request.content.decode("utf-8")
        assert "attachments" in body
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_upload_file_to_channel_files_folder(graph_client: GraphClient) -> None:
    respx.get(f"{GRAPH_BASE_URL}/teams/team-1/channels/channel-1/filesFolder").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "folder-1",
                "parentReference": {"driveId": "drive-1"},
            },
        )
    )
    respx.put(f"{GRAPH_BASE_URL}/drives/drive-1/items/folder-1:/antwort.pdf:/content").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "item-1",
                "name": "antwort.pdf",
                "eTag": '"668f7fa8-8129-4de7-b32b-fe1b442e6ef1",1"',
                "webUrl": "https://contoso.sharepoint.com/sites/team/antwort.pdf",
                "webDavUrl": "https://contoso.sharepoint.com/antwort.pdf",
                "parentReference": {"driveId": "drive-1"},
            },
        )
    )

    await graph_client.start()
    try:
        item = await graph_client.upload_file_to_files_folder(
            filename="antwort.pdf",
            content=b"%PDF-1.4",
            content_type="application/pdf",
            team_id="team-1",
            channel_id="channel-1",
            target_mode="channel",
        )
        assert item["name"] == "antwort.pdf"
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_upload_file_to_teams_chat_files_folder(graph_client: GraphClient) -> None:
    respx.put(
        f"{GRAPH_BASE_URL}/me/drive/root:/Microsoft%20Teams%20Chat%20Files/antwort.pdf:/content"
    ).mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "item-2",
                "name": "antwort.pdf",
                "webUrl": (
                    "https://contoso-my.sharepoint.com/personal/user/"
                    "Documents/Microsoft%20Teams%20Chat%20Files/antwort.pdf"
                ),
                "parentReference": {"driveId": "drive-me"},
            },
        )
    )
    respx.get(f"{GRAPH_BASE_URL}/drives/drive-me/items/item-2").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "item-2",
                "name": "antwort.pdf",
                "eTag": '"668f7fa8-8129-4de7-b32b-fe1b442e6ef1",1"',
                "webUrl": (
                    "https://contoso-my.sharepoint.com/personal/user/"
                    "Documents/Microsoft%20Teams%20Chat%20Files/antwort.pdf"
                ),
                "parentReference": {"driveId": "drive-me"},
            },
        )
    )

    await graph_client.start()
    try:
        item = await graph_client.upload_file_to_teams_chat_files_folder(
            filename="antwort.pdf",
            content=b"%PDF-1.4",
            content_type="application/pdf",
        )
        assert item["name"] == "antwort.pdf"
        assert "Teams%20Chat%20Files" in item["webUrl"]
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_invite_users_to_drive_item(graph_client: GraphClient) -> None:
    route = respx.post(f"{GRAPH_BASE_URL}/drives/drive-me/items/item-2/invite").mock(
        return_value=httpx.Response(200, json={"value": []})
    )

    await graph_client.start()
    try:
        count = await graph_client.invite_users_to_drive_item(
            {
                "id": "item-2",
                "parentReference": {"driveId": "drive-me"},
            },
            user_object_ids=["user-a", "user-b", "user-a"],
        )
        assert count == 2
        assert route.called
        body = route.calls.last.request.content.decode("utf-8")
        assert "user-a" in body
        assert "user-b" in body
        assert '"sendInvitation": false' in body.replace("False", "false") or (
            '"sendInvitation":false' in body.replace(" ", "")
        )
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_get_chat_members(graph_client: GraphClient) -> None:
    respx.get(f"{GRAPH_BASE_URL}/chats/chat-1/members").mock(
        return_value=httpx.Response(
            200,
            json={"value": [{"userId": "u1", "displayName": "Alice"}]},
        )
    )
    await graph_client.start()
    try:
        members = await graph_client.get_chat_members("chat-1")
        assert members[0]["userId"] == "u1"
    finally:
        await graph_client.close()


def test_encode_sharing_url_matches_graph_spec() -> None:
    url = "https://contoso.sharepoint.com/sites/Docs/Shared Documents/file.pdf"
    token = encode_sharing_url(url)
    assert token.startswith("u!")
    raw = token[2:].replace("_", "/").replace("-", "+")
    padding = "=" * (-len(raw) % 4)
    assert base64.b64decode(raw + padding).decode("utf-8") == url


def test_is_sharepoint_or_onedrive_url() -> None:
    assert is_sharepoint_or_onedrive_url(
        "https://contoso.sharepoint.com/sites/Docs/Shared%20Documents/a.pdf"
    )
    assert not is_sharepoint_or_onedrive_url("https://graph.microsoft.com/v1.0/me")


@pytest.mark.asyncio
@respx.mock
async def test_download_sharepoint_attachment_via_shares_api(graph_client: GraphClient) -> None:
    content_url = (
        "https://contoso.sharepoint.com/sites/Docs/Shared Documents/Test-pdf_4%201.pdf"
    )
    share_id = encode_sharing_url(content_url)
    pdf_bytes = b"%PDF-1.4 test"

    respx.get(f"{GRAPH_BASE_URL}/shares/{share_id}/driveItem").mock(
        return_value=httpx.Response(
            200,
            json={
                "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/download/file.pdf",
                "file": {"mimeType": "application/pdf"},
            },
        )
    )
    respx.get("https://contoso.sharepoint.com/download/file.pdf").mock(
        return_value=httpx.Response(200, content=pdf_bytes)
    )

    await graph_client.start()
    try:
        data, content_type = await graph_client.download_binary_url(content_url)
        assert data == pdf_bytes
        assert content_type == "application/pdf"
    finally:
        await graph_client.close()


@pytest.mark.asyncio
@respx.mock
async def test_download_sharepoint_falls_back_after_direct_401(graph_client: GraphClient) -> None:
    content_url = "https://contoso-my.sharepoint.com/personal/user/Documents/file.pdf"
    share_id = encode_sharing_url(content_url)
    pdf_bytes = b"%PDF-1.4 fallback"

    # SharePoint URLs are routed directly to shares API; simulate content-endpoint fallback.
    respx.get(f"{GRAPH_BASE_URL}/shares/{share_id}/driveItem").mock(
        return_value=httpx.Response(200, json={"file": {"mimeType": "application/pdf"}})
    )
    respx.get(f"{GRAPH_BASE_URL}/shares/{share_id}/driveItem/content").mock(
        return_value=httpx.Response(
            200,
            content=pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
    )

    await graph_client.start()
    try:
        data, content_type = await graph_client.download_binary_url(content_url)
        assert data == pdf_bytes
        assert content_type == "application/pdf"
    finally:
        await graph_client.close()
