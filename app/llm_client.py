"""Ollama REST API Client."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.exceptions import (
    OllamaContextTooLargeError,
    OllamaError,
    OllamaImageLoadError,
)
from app.logging_config import get_logger, truncate_text

logger = get_logger(__name__)


class OllamaClient:
    """Asynchroner Client für die Ollama Chat API."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 180,
        keep_alive: str | None = "10m",
        max_retries: int = 4,
        retry_base_seconds: float = 2.0,
        vision_model: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._vision_model = (vision_model or "").strip() or None
        self._timeout = httpx.Timeout(timeout_seconds)
        self._keep_alive = keep_alive
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Startet den HTTP-Client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )

    async def close(self) -> None:
        """Schließt den HTTP-Client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise OllamaError("Ollama-Client ist nicht gestartet.")
        return self._client

    async def health_check(self) -> bool:
        """Prüft, ob Ollama erreichbar ist."""
        try:
            client = self._get_client()
            response = await client.get("/api/tags")
            return response.status_code == 200
        except (httpx.HTTPError, OllamaError):
            return False

    async def list_models(self) -> list[str]:
        """Listet installierte Ollama-Modelle auf."""
        client = self._get_client()
        response = await client.get("/api/tags")
        if response.status_code != 200:
            raise OllamaError(
                f"Ollama-Modellliste konnte nicht abgerufen werden (HTTP {response.status_code}).",
                status_code=response.status_code,
            )
        data = response.json()
        models = data.get("models", [])
        return [str(m.get("name", "")) for m in models if m.get("name")]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        images: list[str] | None = None,
    ) -> str:
        """Sendet eine Chat-Anfrage an Ollama und gibt die Antwort zurück.

        images: optionale Liste von Base64-kodierten Bildern (ohne data:-Prefix).
        """
        chat_messages: list[dict[str, Any]] = []

        if system_prompt:
            chat_messages.append({"role": "system", "content": system_prompt})

        chat_messages.extend(messages)

        # Bilder an die letzte User-Nachricht hängen (Ollama Multimodal-Format)
        if images:
            for msg in reversed(chat_messages):
                if msg.get("role") == "user":
                    msg["images"] = images
                    break
            else:
                chat_messages.append(
                    {
                        "role": "user",
                        "content": "Bitte analysiere die angehängten Bilder.",
                        "images": images,
                    }
                )

        model = self._model
        if images and self._vision_model:
            model = self._vision_model
            logger.info("ollama_using_vision_model", model=model)
        elif images:
            logger.warning(
                "ollama_images_without_vision_model",
                model=model,
                hint="Setzen Sie OLLAMA_VISION_MODEL auf ein Vision-Modell (z. B. qwen2.5vl).",
            )

        payload: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            "stream": False,
        }

        if self._keep_alive:
            payload["keep_alive"] = self._keep_alive

        start_time = time.monotonic()
        response_data = await self._request_with_retry(payload)
        duration = time.monotonic() - start_time

        message = response_data.get("message", {})
        content = message.get("content", "")

        if not content or not str(content).strip():
            raise OllamaError("Ollama hat eine leere Antwort zurückgegeben.")

        logger.info(
            "ollama_chat_completed",
            duration_seconds=round(duration, 2),
            response_preview=truncate_text(str(content)),
            had_images=bool(images),
            model=model,
        )

        return str(content).strip()

    async def _request_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Führt die Chat-Anfrage mit Retry bei Netzwerkfehlern aus."""
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._do_chat_request(payload)
            except OllamaError as exc:
                if isinstance(exc, OllamaContextTooLargeError):
                    raise
                if exc.status_code is not None and 400 <= exc.status_code < 500:
                    raise
                last_error = exc
                if attempt < self._max_retries:
                    wait_time = self._retry_base_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "ollama_retry",
                        attempt=attempt,
                        wait_seconds=wait_time,
                        error=str(exc),
                    )
                    await asyncio.sleep(wait_time)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    wait_time = self._retry_base_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "ollama_network_retry",
                        attempt=attempt,
                        wait_seconds=wait_time,
                        error=str(exc),
                    )
                    await asyncio.sleep(wait_time)

        raise OllamaError(
            f"Ollama-Anfrage nach {self._max_retries} Versuchen fehlgeschlagen: {last_error}"
        )

    async def _do_chat_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Führt eine einzelne Chat-Anfrage aus."""
        client = self._get_client()
        response = await client.post("/api/chat", json=payload)

        if response.status_code >= 400:
            body = response.text[:500]
            lower = body.lower()
            if response.status_code == 400 and (
                "exceed_context_size" in lower
                or "exceeds the available context size" in lower
                or "context length" in lower
            ):
                raise OllamaContextTooLargeError(
                    f"Ollama-Kontext zu groß (HTTP {response.status_code}): {body[:200]}",
                    status_code=response.status_code,
                )
            if response.status_code == 400 and (
                "failed to load image" in lower
                or "failed to load image or audio file" in lower
                or "failed to load audio" in lower
            ):
                raise OllamaImageLoadError(
                    f"Ollama konnte das Bild nicht laden (HTTP {response.status_code}): {body[:200]}",
                    status_code=response.status_code,
                )
            raise OllamaError(
                f"Ollama-Fehler (HTTP {response.status_code}): {body[:200]}",
                status_code=response.status_code,
            )

        data = response.json()
        return dict(data)
