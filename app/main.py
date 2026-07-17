"""FastAPI-Hauptanwendung mit Lifespan-Management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html import escape
from typing import Any

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse

from app import __version__
from app.attachments import AttachmentProcessor
from app.auth import AuthService
from app.config import Settings, get_settings
from app.config_manager import EditableSettings, build_settings_from_form, update_env_file
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
        vision_model=settings.ollama_vision_model or None,
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

    attachment_processor = AttachmentProcessor(
        graph_client=app_state.graph_client,
        settings=settings,
    )

    app_state.worker = PollingWorker(
        settings=settings,
        teams_service=app_state.teams_service,
        ollama_client=app_state.ollama_client,
        repository=app_state.repository,
        message_parser=message_parser,
        attachment_processor=attachment_processor,
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


def _render_config_page(
    settings: Settings,
    *,
    message: str | None = None,
    error: str | None = None,
) -> str:
    editable = EditableSettings.from_settings(settings)
    select_style = (
        "width:100%;padding:10px;border:1px solid #cfd8dc;"
        "border-radius:8px;font-size:14px;"
    )
    info_box = ""
    if message:
        info_box = (
            "<div style='padding:12px;border-radius:8px;background:#e8fff1;"
            "border:1px solid #8bd3a7;margin-bottom:16px;'>"
            f"{escape(message)}</div>"
        )
    if error:
        info_box = (
            "<div style='padding:12px;border-radius:8px;background:#fff0f0;"
            "border:1px solid #e0a0a0;margin-bottom:16px;'>"
            f"{escape(error)}</div>"
        )

    def selected(current: str, expected: str) -> str:
        return "selected" if current == expected else ""

    def row(label: str, name: str, value: str, help_text: str = "", disabled: bool = False) -> str:
        disabled_attr = "disabled" if disabled else ""
        return (
            "<label style='display:block;margin-bottom:14px;'>"
            f"<div style='font-weight:600;margin-bottom:4px;'>{escape(label)}</div>"
            f"<input name='{escape(name)}' value='{escape(value)}' {disabled_attr} "
            "style='width:100%;padding:10px;border:1px solid #cfd8dc;border-radius:8px;"
            "font-size:14px;box-sizing:border-box;' />"
            f"<div style='font-size:12px;color:#555;margin-top:4px;'>{escape(help_text)}</div>"
            "</label>"
        )

    azure_tenant_row = row(
        "AZURE_TENANT_ID",
        "azure_tenant_id",
        editable.azure_tenant_id,
        "Tenant-ID aus Microsoft Entra",
    )
    azure_client_row = row(
        "AZURE_CLIENT_ID",
        "azure_client_id",
        editable.azure_client_id,
        "Client-ID der App-Registrierung",
    )
    chat_id_row = row(
        "TEAMS_CHAT_ID",
        "teams_chat_id",
        editable.teams_chat_id,
        "Legacy: einzelner Chat (optional, zusaetzlich zu TEAMS_CHAT_IDS)",
    )
    team_id_row = row(
        "TEAMS_TEAM_ID",
        "teams_team_id",
        editable.teams_team_id,
        "Legacy: einzelnes Team (optional, zusaetzlich zu TEAMS_CHANNELS)",
    )
    channel_id_row = row(
        "TEAMS_CHANNEL_ID",
        "teams_channel_id",
        editable.teams_channel_id,
        "Legacy: einzelner Kanal (optional, zusaetzlich zu TEAMS_CHANNELS)",
    )
    channels_row = row(
        "TEAMS_CHANNELS",
        "teams_channels",
        editable.teams_channels,
        "Mehrere Kanäle: teamId|channelId,teamId|channelId",
    )
    chat_ids_row = row(
        "TEAMS_CHAT_IDS",
        "teams_chat_ids",
        editable.teams_chat_ids,
        "Mehrere Chats: chatId1,chatId2",
    )
    bot_prefix_row = row(
        "BOT_PREFIX",
        "bot_prefix",
        editable.bot_prefix,
        "Wird bei TRIGGER_MODE=prefix verwendet",
    )
    vision_model_row = row(
        "OLLAMA_VISION_MODEL",
        "ollama_vision_model",
        editable.ollama_vision_model,
        "Optionales Vision-Modell fuer Bilder",
    )

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Teams Local LLM - Konfiguration</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
</head>
<body style="font-family:Arial,sans-serif;background:#f5f7fb;margin:0;padding:24px;color:#1f2937;">
  <div style="max-width:860px;margin:0 auto;background:white;border-radius:14px;padding:28px;
              box-shadow:0 10px 30px rgba(0,0,0,0.08);">
    <h1 style="margin-top:0;">Teams Local LLM - Konfiguration</h1>
    <p style="color:#4b5563;line-height:1.5;">
      Hier koennen Sie die wichtigsten Einstellungen bequem im Browser pflegen.
      Nach dem Speichern bitte die Anwendung neu starten, damit alle Komponenten
      die neuen Werte sicher uebernehmen.
    </p>
    {info_box}
    <form method="post" action="/config">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        {azure_tenant_row}
        {azure_client_row}
      </div>

      <label style="display:block;margin-bottom:14px;">
        <div style="font-weight:600;margin-bottom:4px;">TEAMS_TARGET_MODE</div>
        <select name="teams_target_mode" style="{select_style}">
          <option value="channel" {selected(editable.teams_target_mode, "channel")}>channel</option>
          <option value="chat" {selected(editable.teams_target_mode, "chat")}>chat</option>
        </select>
        <div style="font-size:12px;color:#555;margin-top:4px;">
          channel = Team-Kanal, chat = Gruppen- oder 1:1-Chat
        </div>
      </label>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        {channels_row}
        {chat_ids_row}
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        {chat_id_row}
        {team_id_row}
      </div>
      {channel_id_row}

      <label style="display:block;margin-bottom:14px;">
        <div style="font-weight:600;margin-bottom:4px;">TRIGGER_MODE</div>
        <select name="trigger_mode" style="{select_style}">
          <option value="all" {selected(editable.trigger_mode, "all")}>all</option>
          <option value="prefix" {selected(editable.trigger_mode, "prefix")}>prefix</option>
          <option value="mention" {selected(editable.trigger_mode, "mention")}>mention</option>
        </select>
      </label>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        {bot_prefix_row}
        {vision_model_row}
      </div>

      <div style="padding:14px;background:#f8fafc;border-radius:10px;
                  border:1px solid #e2e8f0;margin:18px 0;">
        <strong>Hinweis:</strong> Die Felder werden in die bestehende <code>.env</code> geschrieben.
        Andere Werte wie OLLAMA_MODEL, Datenbankpfade oder Retry-Konfiguration bleiben unveraendert.
      </div>

      <button type="submit"
        style="padding:12px 18px;border:none;border-radius:8px;background:#2563eb;color:white;
               font-size:14px;font-weight:700;cursor:pointer;">
        Konfiguration speichern
      </button>
    </form>
  </div>
</body>
</html>"""


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Einfacher Health-Check."""
    return HealthResponse()


@app.get("/config", response_class=HTMLResponse)
async def config_page() -> HTMLResponse:
    """Zeigt eine einfache lokale Konfigurationsoberfläche an."""
    settings = app_state.settings or get_settings()
    return HTMLResponse(_render_config_page(settings))


@app.post("/config", response_class=HTMLResponse)
async def save_config(
    azure_tenant_id: str = Form(""),
    azure_client_id: str = Form(""),
    teams_target_mode: str = Form("channel"),
    teams_chat_id: str = Form(""),
    teams_team_id: str = Form(""),
    teams_channel_id: str = Form(""),
    teams_channels: str = Form(""),
    teams_chat_ids: str = Form(""),
    trigger_mode: str = Form("all"),
    bot_prefix: str = Form("/ai"),
    ollama_vision_model: str = Form(""),
) -> HTMLResponse:
    """Speichert editierbare Konfigurationswerte in die lokale .env-Datei."""
    current_settings = app_state.settings or get_settings()

    try:
        editable = build_settings_from_form(
            current_settings=current_settings,
            azure_tenant_id=azure_tenant_id,
            azure_client_id=azure_client_id,
            teams_target_mode=teams_target_mode,
            teams_chat_id=teams_chat_id,
            teams_team_id=teams_team_id,
            teams_channel_id=teams_channel_id,
            teams_channels=teams_channels,
            teams_chat_ids=teams_chat_ids,
            trigger_mode=trigger_mode,
            bot_prefix=bot_prefix,
            ollama_vision_model=ollama_vision_model,
        )
    except ValueError as exc:
        return HTMLResponse(
            _render_config_page(current_settings, error=str(exc)),
            status_code=400,
        )

    env_path = current_settings.token_cache_path_obj.parent.parent / ".env"
    update_env_file(env_path, editable.to_env_map())

    merged_values = current_settings.model_dump()
    merged_values.update(
        {
            "azure_tenant_id": editable.azure_tenant_id,
            "azure_client_id": editable.azure_client_id,
            "teams_target_mode": editable.teams_target_mode,
            "teams_chat_id": editable.teams_chat_id,
            "teams_team_id": editable.teams_team_id,
            "teams_channel_id": editable.teams_channel_id,
            "trigger_mode": editable.trigger_mode,
            "bot_prefix": editable.bot_prefix,
            "ollama_vision_model": editable.ollama_vision_model,
        }
    )
    app_state.settings = Settings(**merged_values)

    return HTMLResponse(
        _render_config_page(
            app_state.settings,
            message=(
                "Konfiguration gespeichert. Bitte die Anwendung neu starten, "
                "damit Authentifizierung, Worker und Ollama-Client die neuen Werte verwenden."
            ),
        )
    )


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
        teams_target_mode=app_state.settings.teams_target_mode.value,
        configured_team_id=app_state.settings.teams_team_id or None,
        configured_channel_id=app_state.settings.teams_channel_id or None,
        configured_chat_id=app_state.settings.teams_chat_id or None,
        configured_targets=[t.key for t in app_state.settings.resolved_targets],
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
