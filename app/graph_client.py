"""Microsoft Graph REST API Client über httpx."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.exceptions import GraphAPIError, GraphPermissionError
from app.logging_config import get_logger

logger = get_logger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


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

    async def send_reply(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        html_content: str,
    ) -> dict[str, Any]:
        """Sendet eine Thread-Antwort unter einer Kanalnachricht."""
        body = {
            "body": {
                "contentType": "html",
                "content": html_content,
            }
        }
        return await self._request(
            "POST",
            f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
            json=body,
        )

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
