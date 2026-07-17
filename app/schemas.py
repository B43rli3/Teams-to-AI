"""Pydantic-Schemas für API-Antworten."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app import __version__


class HealthResponse(BaseModel):
    status: str = "ok"


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]
    message: str | None = None


class StatusResponse(BaseModel):
    worker_running: bool
    last_successful_poll: str | None
    last_poll_error: str | None
    authenticated_user_display_name: str | None
    teams_target_mode: str | None = None
    configured_team_id: str | None
    configured_channel_id: str | None
    configured_chat_id: str | None = None
    configured_targets: list[str] = Field(default_factory=list)
    ollama_model: str
    queued_messages: int
    failed_messages: int
    completed_messages: int
    application_version: str = Field(default=__version__)


class PollNowResponse(BaseModel):
    success: bool
    message: str
    new_messages: int = 0


class RecentErrorsResponse(BaseModel):
    errors: list[dict[str, Any]]
