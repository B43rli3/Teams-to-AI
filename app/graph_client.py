"""Microsoft Graph REST API Client über httpx."""

from __future__ import annotations

import asyncio
import base64
import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from app.exceptions import GraphAPIError, GraphPermissionError
from app.logging_config import get_logger

logger = get_logger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

_DRIVE_ITEM_ATTACHMENT_SELECT = "id,name,eTag,webUrl,webDavUrl,file,parentReference"

TEAMS_CHAT_FILES_FOLDER_CANDIDATES = (
    "Microsoft Teams Chat Files",
    "Microsoft Teams-Chatdateien",
)

_SHARE_HOST_MARKERS = (
    "sharepoint.com",
    "sharepoint.de",
    "onedrive.com",
    "onedrive.live.com",
    "1drv.ms",
)


def encode_sharing_url(url: str) -> str:
    """Kodiert eine SharePoint-/OneDrive-URL für die Graph-Shares-API."""
    encoded = base64.b64encode(url.encode("utf-8")).decode("ascii")
    encoded = encoded.rstrip("=").replace("/", "_").replace("+", "-")
    return f"u!{encoded}"


def is_sharepoint_or_onedrive_url(url: str) -> bool:
    """Erkennt URLs, die typischerweise über /shares/.../driveItem geladen werden."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(marker in host for marker in _SHARE_HOST_MARKERS)


class GraphClient:
    """Asynchroner Client für Microsoft Graph API."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        token_refresher: Callable[[], str],
        max_retries: int = 4,
        retry_base_seconds: float = 2.0,
    ) -> None:
        self._token_provider = token_provider
        self._token_refresher = token_refresher
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._client: httpx.AsyncClient | None = None
        self._token_refreshed_for_request = False

    async def start(self) -> None:
        """Startet den HTTP-Client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=GRAPH_BASE_URL,
                timeout=httpx.Timeout(60.0),
            )

    async def close(self) -> None:
        """Schließt den HTTP-Client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise GraphAPIError("Graph-Client ist nicht gestartet.")
        return self._client

    async def get_me(self) -> dict[str, Any]:
        """Ruft den aktuellen Benutzer ab."""
        return await self._request("GET", "/me")

    async def get_joined_teams(self) -> list[dict[str, Any]]:
        """Listet Teams des angemeldeten Benutzers auf."""
        data = await self._request("GET", "/me/joinedTeams")
        return list(data.get("value", []))

    async def get_team_channels(self, team_id: str) -> list[dict[str, Any]]:
        """Listet Kanäle eines Teams auf."""
        data = await self._request("GET", f"/teams/{team_id}/channels")
        return list(data.get("value", []))

    async def get_joined_chats(self, *, top: int = 50) -> list[dict[str, Any]]:
        """Listet Chats des angemeldeten Benutzers auf."""
        data = await self._request(
            "GET",
            "/me/chats",
            params={"$top": str(top), "$expand": "members"},
        )
        return list(data.get("value", []))

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        """Ruft Metadaten eines Chats ab."""
        return await self._request("GET", f"/chats/{chat_id}")

    async def get_channel_messages(
        self,
        team_id: str,
        channel_id: str,
        *,
        top: int = 20,
    ) -> list[dict[str, Any]]:
        """Ruft Kanalnachrichten ab."""
        data = await self._request(
            "GET",
            f"/teams/{team_id}/channels/{channel_id}/messages",
            params={"$top": str(top)},
        )
        return list(data.get("value", []))

    async def get_chat_messages(
        self,
        chat_id: str,
        *,
        top: int = 20,
    ) -> list[dict[str, Any]]:
        """Ruft Nachrichten eines Gruppen- oder 1:1-Chats ab."""
        data = await self._request(
            "GET",
            f"/chats/{chat_id}/messages",
            params={"$top": str(top)},
        )
        return list(data.get("value", []))

    async def send_channel_reply(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        html_content: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Sendet eine Thread-Antwort unter einer Kanalnachricht."""
        body: dict[str, Any] = {
            "body": {
                "contentType": "html",
                "content": html_content,
            }
        }
        if attachments:
            body["attachments"] = attachments
        return await self._request(
            "POST",
            f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
            json=body,
        )

    async def send_chat_reply(
        self,
        chat_id: str,
        message_id: str,
        html_content: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Sendet eine Antwort in einen Chat.

        Hinweis: Gruppen-/1:1-Chats unterstützen im Gegensatz zu Kanälen keinen
        Thread-Endpunkt `/messages/{id}/replies` (HTTP 405). Antworten werden
        daher als neue Chat-Nachricht über `POST /chats/{id}/messages` gesendet.
        `message_id` bleibt für die Signatur/Kompatibilität erhalten.
        """
        _ = message_id  # In Chats gibt es keine Thread-Replies wie in Kanälen.
        body: dict[str, Any] = {
            "body": {
                "contentType": "html",
                "content": html_content,
            }
        }
        if attachments:
            body["attachments"] = attachments
        return await self._request(
            "POST",
            f"/chats/{chat_id}/messages",
            json=body,
        )

    async def get_channel_files_folder(self, team_id: str, channel_id: str) -> dict[str, Any]:
        """Ruft den SharePoint-Dateiordner eines Kanals ab."""
        return await self._request(
            "GET",
            f"/teams/{team_id}/channels/{channel_id}/filesFolder",
            params={"$select": "id,name,parentReference,webUrl"},
        )

    async def get_chat_files_folder(self, chat_id: str) -> dict[str, Any]:
        """Ruft den Dateiordner eines Chats ab."""
        return await self._request(
            "GET",
            f"/chats/{chat_id}/filesFolder",
            params={"$select": "id,name,parentReference,webUrl"},
        )

    async def upload_file_to_files_folder(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        team_id: str | None = None,
        channel_id: str | None = None,
        chat_id: str | None = None,
        target_mode: Any = "channel",
    ) -> dict[str, Any]:
        """Lädt eine Datei in den Teams-Dateiordner (Kanal oder Chat) hoch."""
        mode_value = getattr(target_mode, "value", str(target_mode))
        if mode_value == "chat":
            if not chat_id:
                raise GraphAPIError("TEAMS_CHAT_ID fehlt für Datei-Upload.")
            folder = await self.get_chat_files_folder(chat_id)
        else:
            if not team_id or not channel_id:
                raise GraphAPIError("Team- und Channel-ID fehlen für Datei-Upload.")
            folder = await self.get_channel_files_folder(team_id, channel_id)

        parent_ref = folder.get("parentReference", {}) or {}
        drive_id = str(parent_ref.get("driveId") or "")
        folder_id = str(folder.get("id") or "")
        if not drive_id or not folder_id:
            raise GraphAPIError("Dateiordner konnte nicht aufgelöst werden.")

        from urllib.parse import quote

        encoded_name = quote(filename, safe="")
        path = f"/drives/{drive_id}/items/{folder_id}:/{encoded_name}:/content"
        drive_item = await self._upload_bytes(
            path,
            content,
            content_type=content_type,
            params={"$select": "id,name,webUrl,webDavUrl,file,parentReference"},
        )
        return await self._finalize_uploaded_drive_item(drive_item)

    async def upload_file_to_teams_chat_files_folder(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        """Lädt eine Datei in den OneDrive-Ordner für Teams-Chats hoch."""
        from urllib.parse import quote

        encoded_name = quote(filename, safe="")
        last_error: Exception | None = None

        for folder_name in TEAMS_CHAT_FILES_FOLDER_CANDIDATES:
            encoded_folder = quote(folder_name, safe="")
            path = f"/me/drive/root:/{encoded_folder}/{encoded_name}:/content"
            try:
                drive_item = await self._upload_bytes(
                    path,
                    content,
                    content_type=content_type,
                    params={"$select": "id,name,webUrl,webDavUrl,file,parentReference"},
                )
                return await self._finalize_uploaded_drive_item(drive_item)
            except GraphAPIError as exc:
                last_error = exc
                continue

        raise GraphAPIError(
            "Upload in den Teams-Chat-Dateiordner fehlgeschlagen.",
        ) from last_error

    async def upload_file_to_me_drive_root(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any]:
        """Lädt eine Datei in das OneDrive des angemeldeten Benutzers hoch."""
        from urllib.parse import quote

        encoded_name = quote(filename, safe="")
        path = f"/me/drive/root:/{encoded_name}:/content"
        drive_item = await self._upload_bytes(
            path,
            content,
            content_type=content_type,
            params={"$select": "id,name,webUrl,webDavUrl,file,parentReference"},
        )
        return await self._finalize_uploaded_drive_item(drive_item)

    async def create_organization_view_link(self, drive_item: dict[str, Any]) -> str | None:
        """Erstellt einen organisationsweiten Lese-Link für ein driveItem."""
        item_id = str(drive_item.get("id") or "")
        parent = drive_item.get("parentReference", {}) or {}
        drive_id = str(parent.get("driveId") or "")
        if not item_id or not drive_id:
            return None

        result = await self._request(
            "POST",
            f"/drives/{drive_id}/items/{item_id}/createLink",
            json={"type": "view", "scope": "organization"},
        )
        link = result.get("link", {}) or {}
        web_url = str(link.get("webUrl") or "").strip()
        return web_url or None

    async def _finalize_uploaded_drive_item(self, drive_item: dict[str, Any]) -> dict[str, Any]:
        """Lädt fehlende Metadaten (eTag, webUrl) nach dem Upload nach."""
        if not drive_item:
            return drive_item

        has_urls = bool(drive_item.get("webUrl") or drive_item.get("webDavUrl"))
        has_etag = bool(drive_item.get("eTag"))
        if has_urls and has_etag:
            return drive_item

        item_id = str(drive_item.get("id") or "")
        if not item_id:
            return drive_item

        parent = drive_item.get("parentReference", {}) or {}
        drive_id = str(parent.get("driveId") or "")
        if drive_id:
            path = f"/drives/{drive_id}/items/{item_id}"
        else:
            path = f"/me/drive/items/{item_id}"

        try:
            return await self._request(
                "GET",
                path,
                params={"$select": _DRIVE_ITEM_ATTACHMENT_SELECT},
            )
        except GraphAPIError:
            logger.warning(
                "drive_item_finalize_failed",
                item_id=item_id[:12],
            )
            return drive_item

    async def send_reply(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        html_content: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Alias für send_channel_reply (Abwärtskompatibilität)."""
        return await self.send_channel_reply(
            team_id,
            channel_id,
            message_id,
            html_content,
            attachments=attachments,
        )

    async def _upload_bytes(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Lädt binäre Inhalte per PUT auf Graph hoch."""
        self._token_refreshed_for_request = False
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._do_upload_bytes(
                    path,
                    data,
                    content_type=content_type,
                    params=params,
                )
            except GraphPermissionError:
                raise
            except GraphAPIError as exc:
                last_error = exc
                if exc.status_code == 429:
                    await asyncio.sleep(exc.retry_after or 30)
                    continue
                if exc.status_code == 401 and not self._token_refreshed_for_request:
                    self._token_refresher()
                    self._token_refreshed_for_request = True
                    continue
                if exc.status_code and exc.status_code >= 500 and attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_with_jitter(attempt))
                    continue
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_with_jitter(attempt))
                    continue

        raise GraphAPIError(f"Upload fehlgeschlagen nach Retries: {last_error}")

    async def _do_upload_bytes(
        self,
        path: str,
        data: bytes,
        *,
        content_type: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        token = self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        }
        response = await client.put(path, headers=headers, content=data, params=params)

        if response.status_code == 401:
            raise GraphAPIError("Nicht autorisiert (401) beim Upload.", status_code=401)
        if response.status_code == 403:
            raise GraphPermissionError(
                "Zugriff verweigert (403) beim Datei-Upload. "
                "Prüfen Sie Graph-Berechtigungen (Files.ReadWrite, ggf. Sites.ReadWrite.All).",
                status_code=403,
            )
        if response.status_code == 429:
            raise GraphAPIError(
                "Rate Limit (429) beim Upload.",
                status_code=429,
                retry_after=int(response.headers.get("Retry-After", "30")),
            )
        if response.status_code >= 400:
            error_data = self._parse_error(response)
            raise GraphAPIError(
                f"Upload-Fehler (HTTP {response.status_code}): "
                f"{error_data.get('message', response.text[:200])}",
                status_code=response.status_code,
            )

        if response.status_code == 204:
            return {}
        return dict(response.json())

    def _message_base_path(
        self,
        *,
        message_id: str,
        team_id: str | None,
        channel_id: str | None,
        chat_id: str | None,
        target_mode: Any,
    ) -> str:
        mode_value = getattr(target_mode, "value", str(target_mode))
        if mode_value == "chat":
            if not chat_id:
                raise GraphAPIError("TEAMS_CHAT_ID fehlt für Hosted-Content-Download.")
            return f"/chats/{chat_id}/messages/{message_id}"
        if not team_id or not channel_id:
            raise GraphAPIError("Team- und Channel-ID fehlen für Hosted-Content-Download.")
        return f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}"

    async def list_hosted_content_ids(
        self,
        *,
        message_id: str,
        team_id: str | None = None,
        channel_id: str | None = None,
        chat_id: str | None = None,
        target_mode: Any = "channel",
    ) -> list[str]:
        """Listet Hosted-Content-IDs einer Nachricht auf."""
        base = self._message_base_path(
            message_id=message_id,
            team_id=team_id,
            channel_id=channel_id,
            chat_id=chat_id,
            target_mode=target_mode,
        )
        data = await self._request("GET", f"{base}/hostedContents")
        values = data.get("value", [])
        return [str(item.get("id")) for item in values if item.get("id")]

    async def download_hosted_content(
        self,
        *,
        message_id: str,
        hosted_content_id: str,
        team_id: str | None = None,
        channel_id: str | None = None,
        chat_id: str | None = None,
        target_mode: Any = "channel",
    ) -> tuple[bytes, str]:
        """Lädt Hosted Content (z. B. Inline-Bilder) herunter."""
        from urllib.parse import quote

        base = self._message_base_path(
            message_id=message_id,
            team_id=team_id,
            channel_id=channel_id,
            chat_id=chat_id,
            target_mode=target_mode,
        )
        encoded_id = quote(hosted_content_id, safe="")
        path = f"{base}/hostedContents/{encoded_id}/$value"
        return await self._download_bytes(path)

    async def download_binary_url(self, url: str) -> tuple[bytes, str]:
        """Lädt binäre Inhalte über Graph, Shares-API oder absolute URLs herunter."""
        if is_sharepoint_or_onedrive_url(url):
            return await self._download_via_shares_api(url)

        try:
            return await self._download_bytes(url)
        except GraphAPIError as exc:
            if exc.status_code == 401:
                logger.info(
                    "attachment_direct_download_unauthorized_try_shares",
                    url_host=urlparse(url).netloc or "unknown",
                )
                return await self._download_via_shares_api(url)
            raise

    async def _download_via_shares_api(self, sharing_url: str) -> tuple[bytes, str]:
        """Lädt SharePoint-/OneDrive-Dateien über GET /shares/.../driveItem."""
        share_id = encode_sharing_url(sharing_url)
        item = await self._request("GET", f"/shares/{share_id}/driveItem")
        download_url = item.get("@microsoft.graph.downloadUrl")
        if download_url:
            content_type = str(item.get("file", {}).get("mimeType") or "application/octet-stream")
            data = await self._download_public_url(str(download_url))
            return data, content_type

        return await self._download_bytes(f"/shares/{share_id}/driveItem/content")

    async def _download_public_url(self, url: str) -> bytes:
        """Lädt eine vorauthentifizierte Download-URL ohne Bearer-Token."""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(60.0),
                    follow_redirects=True,
                ) as client:
                    response = await client.get(url)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "30"))
                    await asyncio.sleep(retry_after)
                    continue
                if response.status_code >= 400:
                    raise GraphAPIError(
                        f"Download-Fehler (HTTP {response.status_code})",
                        status_code=response.status_code,
                    )
                return response.content
            except GraphAPIError:
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_with_jitter(attempt))
                    continue
        raise GraphAPIError(f"Download fehlgeschlagen nach Retries: {last_error}")

    async def _download_bytes(self, url_or_path: str) -> tuple[bytes, str]:
        """Lädt Bytes mit Auth und Retry-Logik."""
        self._token_refreshed_for_request = False
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._do_download_bytes(url_or_path)
            except GraphPermissionError:
                raise
            except GraphAPIError as exc:
                last_error = exc
                if exc.status_code == 429:
                    await asyncio.sleep(exc.retry_after or 30)
                    continue
                if exc.status_code == 401 and not self._token_refreshed_for_request:
                    self._token_refresher()
                    self._token_refreshed_for_request = True
                    continue
                if exc.status_code and exc.status_code >= 500 and attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_with_jitter(attempt))
                    continue
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_with_jitter(attempt))
                    continue

        raise GraphAPIError(f"Download fehlgeschlagen nach Retries: {last_error}")

    async def _do_download_bytes(self, url_or_path: str) -> tuple[bytes, str]:
        client = self._get_client()
        token = self._token_provider()
        headers = {"Authorization": f"Bearer {token}"}

        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            response = await client.get(url_or_path, headers=headers)
        else:
            response = await client.get(url_or_path, headers=headers)

        if response.status_code == 401:
            raise GraphAPIError("Nicht autorisiert (401) beim Download.", status_code=401)
        if response.status_code == 403:
            raise GraphPermissionError(
                "Zugriff verweigert (403) beim Anhangs-Download. "
                "Prüfen Sie Graph-Berechtigungen (ggf. Files.Read.All für SharePoint-Dateien).",
                status_code=403,
            )
        if response.status_code == 429:
            raise GraphAPIError(
                "Rate Limit (429) beim Download.",
                status_code=429,
                retry_after=int(response.headers.get("Retry-After", "30")),
            )
        if response.status_code >= 400:
            raise GraphAPIError(
                f"Download-Fehler (HTTP {response.status_code})",
                status_code=response.status_code,
            )

        content_type = response.headers.get("Content-Type", "application/octet-stream")
        return response.content, content_type

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Führt einen Graph API Request mit Retry-Logik aus."""
        self._token_refreshed_for_request = False
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                start_time = time.monotonic()
                result = await self._do_request(method, path, params=params, json=json)
                duration = time.monotonic() - start_time
                logger.debug(
                    "graph_request_completed",
                    method=method,
                    path=path,
                    duration_seconds=round(duration, 2),
                )
                return result
            except GraphPermissionError:
                raise
            except GraphAPIError as exc:
                last_error = exc

                if exc.status_code == 429:
                    retry_after = exc.retry_after or 30
                    logger.warning(
                        "graph_rate_limited",
                        retry_after=retry_after,
                        attempt=attempt,
                    )
                    await asyncio.sleep(retry_after)
                    continue

                if exc.status_code == 401 and not self._token_refreshed_for_request:
                    logger.info("graph_token_refresh_attempt")
                    self._token_refresher()
                    self._token_refreshed_for_request = True
                    continue

                if exc.status_code and exc.status_code >= 500 and attempt < self._max_retries:
                    wait = self._backoff_with_jitter(attempt)
                    logger.warning(
                        "graph_server_error_retry",
                        status_code=exc.status_code,
                        attempt=attempt,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    wait = self._backoff_with_jitter(attempt)
                    logger.warning(
                        "graph_network_error_retry",
                        attempt=attempt,
                        wait_seconds=wait,
                        error=str(exc),
                    )
                    await asyncio.sleep(wait)
                    continue

        raise GraphAPIError(
            f"Graph-Anfrage nach {self._max_retries} Versuchen fehlgeschlagen: {last_error}"
        )

    async def _do_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Führt eine einzelne Graph API Anfrage aus."""
        client = self._get_client()
        token = self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        response = await client.request(
            method,
            path,
            headers=headers,
            params=params,
            json=json,
        )

        if response.status_code == 401:
            error_data = self._parse_error(response)
            raise GraphAPIError(
                f"Nicht autorisiert (401): {error_data.get('message', 'Token ungültig')}",
                status_code=401,
                error_code=error_data.get("code"),
            )

        if response.status_code == 403:
            error_data = self._parse_error(response)
            raise GraphPermissionError(
                "Zugriff verweigert (403). Bitte prüfen Sie die delegierten "
                "Graph-Berechtigungen (ChannelMessage.Read.All, ChannelMessage.Send) "
                "und ob ein Admin Consent erteilt wurde.",
                status_code=403,
                error_code=error_data.get("code"),
            )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "30"))
            raise GraphAPIError(
                "Rate Limit erreicht (429).",
                status_code=429,
                retry_after=retry_after,
            )

        if response.status_code >= 400:
            error_data = self._parse_error(response)
            raise GraphAPIError(
                f"Graph-Fehler (HTTP {response.status_code}): "
                f"{error_data.get('message', response.text[:200])}",
                status_code=response.status_code,
                error_code=error_data.get("code"),
            )

        if response.status_code == 204:
            return {}

        return dict(response.json())

    def _backoff_with_jitter(self, attempt: int) -> float:
        """Berechnet exponentielles Backoff mit Jitter."""
        base = self._retry_base_seconds * (2 ** (attempt - 1))
        return float(base + random.uniform(0, 1))

    @staticmethod
    def _parse_error(response: httpx.Response) -> dict[str, Any]:
        """Parst Graph-Fehlerantworten."""
        try:
            data = response.json()
            error = data.get("error", data)
            if isinstance(error, dict):
                return {
                    "code": error.get("code", ""),
                    "message": error.get("message", ""),
                }
        except Exception:
            pass
        return {"code": "", "message": response.text[:200]}
