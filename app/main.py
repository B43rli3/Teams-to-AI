"""FastAPI-Hauptanwendung mit Lifespan-Management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from app import __version__
from app.auth import AuthService
from app.config import Settings, get_settings
from app.graph_client import GraphClient
from app.llm_client import OllamaClient
from app.logging_config import configure_logging, get_logger
from app.message_parser import MessageParser
from app.repository import Repository
from app.schemas import (
    HealthResponse,
    PollNowResponse,
    ReadyResponse,
    RecentErrorsResponse,
    StatusResponse,
)
from app.teams_service import TeamsService
from app.worker import PollingWorker

logger = get_logger(__name__)


class AppState:
    """Hält den gemeinsamen Anwendungszustand."""

    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.auth_service: AuthService | None = None
        self.graph_client: GraphClient | None = None
        self.ollama_client: OllamaClient | None = None
        self.repository: Repository | None = None
        self.teams_service: TeamsService | None = None
        self.worker: PollingWorker | None = None
        self.authenticated_user: dict[str, Any] | None = None
        self._current_token: str = ""

    def get_token(self) -> str:
        if not self._current_token and self.auth_service:
            self._current_token = self.auth_service.get_access_token()
        return self._current_token

    def refresh_token(self) -> str:
        if self.auth_service:
            silent = self.auth_service.acquire_token_silent()
            if silent and "access_token" in silent:
                self._current_token = str(silent["access_token"])
            else:
                self._current_token = self.auth_service.get_access_token()
        return self._current_token


app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup und Shutdown der Anwendung."""
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_for_runtime()
    settings.ensure_data_dir()

    logger.info("application_starting", version=__version__)

    app_state.settings = settings

    app_state.auth_service = AuthService(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        scopes=settings.graph_scope_list,
        cache_path=settings.token_cache_path_obj,
    )

    app_state.graph_client = GraphClient(
        token_provider=app_state.get_token,
        token_refresher=app_state.refresh_token,
        max_retries=settings.http_max_retries,
        retry_base_seconds=settings.http_retry_base_seconds,
    )
    await app_state.graph_client.start()

    app_state.ollama_client = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
        max_retries=settings.http_max_retries,
        retry_base_seconds=settings.http_retry_base_seconds,
    )
    await app_state.ollama_client.start()

    app_state.repository = Repository(settings.database_path)
    await app_state.repository.connect()

    token = app_state.get_token()
    app_state._current_token = token
    me = await app_state.graph_client.get_me()
    app_state.authenticated_user = me
    logger.info(
        "authenticated_user",
        display_name=me.get("displayName", "Unbekannt"),
    )

    message_parser = MessageParser(
        max_response_characters=settings.llm_max_response_characters,
    )

    app_state.teams_service = TeamsService(
        graph_client=app_state.graph_client,
        settings=settings,
        message_parser=message_parser,
        authenticated_user_id=str(me.get("id", "")),
    )

    app_state.worker = PollingWorker(
        settings=settings,
        teams_service=app_state.teams_service,
        ollama_client=app_state.ollama_client,
        repository=app_state.repository,
        message_parser=message_parser,
    )
    await app_state.worker.start()

    logger.info("application_started")

    yield

    logger.info("application_shutting_down")

    if app_state.worker:
        await app_state.worker.stop()
    if app_state.ollama_client:
        await app_state.ollama_client.close()
    if app_state.graph_client:
        await app_state.graph_client.close()
    if app_state.auth_service:
        app_state.auth_service.save_cache()
    if app_state.repository:
        await app_state.repository.close()

    logger.info("application_stopped")


app = FastAPI(
    title="Teams Local LLM",
    description="Lokaler Microsoft Teams Kanal-Assistent mit Ollama",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Einfacher Health-Check."""
    return HealthResponse()


@app.get("/ready", response_model=ReadyResponse)
async def ready() -> ReadyResponse:
    """Prüft Bereitschaft aller Abhängigkeiten."""
    checks: dict[str, bool] = {}

    if app_state.settings:
        try:
            app_state.settings.validate_for_runtime()
            checks["config"] = True
        except Exception:
            checks["config"] = False
    else:
        checks["config"] = False

    if app_state.repository:
        checks["database"] = await app_state.repository.health_check()
    else:
        checks["database"] = False

    if app_state.ollama_client:
        checks["ollama"] = await app_state.ollama_client.health_check()
    else:
        checks["ollama"] = False

    checks["auth"] = app_state.authenticated_user is not None

    all_ready = all(checks.values())
    return ReadyResponse(
        ready=all_ready,
        checks=checks,
        message=None if all_ready else "Nicht alle Abhängigkeiten sind bereit.",
    )


@app.get("/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    """Liefert den aktuellen Anwendungsstatus."""
    if not app_state.settings or not app_state.repository or not app_state.worker:
        raise HTTPException(status_code=503, detail="Anwendung nicht initialisiert.")

    queued = await app_state.repository.count_by_status("queued")
    failed = await app_state.repository.count_by_status("failed")
    completed = await app_state.repository.count_by_status("completed")

    user_name = None
    if app_state.authenticated_user:
        user_name = str(app_state.authenticated_user.get("displayName", ""))

    return StatusResponse(
        worker_running=app_state.worker.is_running,
        last_successful_poll=app_state.worker.last_successful_poll,
        last_poll_error=app_state.worker.last_poll_error,
        authenticated_user_display_name=user_name,
        configured_team_id=app_state.settings.teams_team_id or None,
        configured_channel_id=app_state.settings.teams_channel_id or None,
        ollama_model=app_state.settings.ollama_model,
        queued_messages=queued,
        failed_messages=failed,
        completed_messages=completed,
        application_version=__version__,
    )


@app.post("/poll-now", response_model=PollNowResponse)
async def poll_now() -> PollNowResponse:
    """Löst einen sofortigen Poll aus."""
    if not app_state.worker:
        raise HTTPException(status_code=503, detail="Worker nicht verfügbar.")

    if app_state.worker._poll_lock.locked():
        return PollNowResponse(
            success=False,
            message="Ein Poll läuft bereits.",
        )

    try:
        new_count = await app_state.worker.poll_now()
        return PollNowResponse(
            success=True,
            message="Poll erfolgreich abgeschlossen.",
            new_messages=new_count,
        )
    except Exception as exc:
        return PollNowResponse(
            success=False,
            message=f"Poll fehlgeschlagen: {str(exc)[:200]}",
        )


@app.get("/recent-errors", response_model=RecentErrorsResponse)
async def recent_errors() -> RecentErrorsResponse:
    """Liefert kürzliche Fehler ohne sensible Inhalte."""
    if not app_state.repository:
        raise HTTPException(status_code=503, detail="Datenbank nicht verfügbar.")

    errors = await app_state.repository.get_recent_errors()
    return RecentErrorsResponse(errors=errors)
