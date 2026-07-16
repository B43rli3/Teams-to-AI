"""Tests fuer .env-Schreiben und UI-Konfigurationsdaten."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.config_manager import (
    EditableSettings,
    build_settings_from_form,
    update_env_file,
)
from app.main import _render_config_page


def test_update_env_file_updates_only_selected_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "AZURE_TENANT_ID=old-tenant\n"
        "AZURE_CLIENT_ID=old-client\n"
        "UNCHANGED_KEY=keep\n",
        encoding="utf-8",
    )

    update_env_file(
        env_path,
        {
            "AZURE_TENANT_ID": "new-tenant",
            "BOT_PREFIX": "/ai",
        },
    )

    content = env_path.read_text(encoding="utf-8")
    assert "AZURE_TENANT_ID=new-tenant" in content
    assert "AZURE_CLIENT_ID=old-client" in content
    assert "UNCHANGED_KEY=keep" in content
    assert "BOT_PREFIX=/ai" in content


def test_build_settings_from_form_uses_existing_prefix_as_fallback() -> None:
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_team_id="team",
        teams_channel_id="channel",
        bot_prefix="/ai",
    )

    editable = build_settings_from_form(
        current_settings=settings,
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_target_mode="channel",
        teams_chat_id="",
        teams_team_id="team",
        teams_channel_id="channel",
        trigger_mode="prefix",
        bot_prefix="   ",
        ollama_vision_model="",
    )

    assert editable.bot_prefix == "/ai"


def test_build_settings_from_form_normalizes_values() -> None:
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_team_id="team",
        teams_channel_id="channel",
    )

    editable = build_settings_from_form(
        current_settings=settings,
        azure_tenant_id=" tenant-new ",
        azure_client_id=" client-new ",
        teams_target_mode="CHAT",
        teams_chat_id=" 19:test@thread.v2 ",
        teams_team_id="",
        teams_channel_id="",
        trigger_mode="PREFIX",
        bot_prefix=" /ki ",
        ollama_vision_model=" qwen2.5vl:7b ",
    )

    assert editable == EditableSettings(
        azure_tenant_id="tenant-new",
        azure_client_id="client-new",
        teams_target_mode="chat",
        teams_chat_id="19:test@thread.v2",
        teams_team_id="",
        teams_channel_id="",
        trigger_mode="prefix",
        bot_prefix="/ki",
        ollama_vision_model="qwen2.5vl:7b",
    )


def test_render_config_page_contains_requested_fields() -> None:
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        teams_team_id="team",
        teams_channel_id="channel",
        bot_prefix="/ai",
        ollama_vision_model="qwen2.5vl:7b",
    )

    html = _render_config_page(settings, message="Gespeichert")
    assert "AZURE_TENANT_ID" in html
    assert "AZURE_CLIENT_ID" in html
    assert "TEAMS_TARGET_MODE" in html
    assert "TEAMS_CHAT_ID" in html
    assert "TEAMS_TEAM_ID" in html
    assert "TEAMS_CHANNEL_ID" in html
    assert "TRIGGER_MODE" in html
    assert "BOT_PREFIX" in html
    assert "OLLAMA_VISION_MODEL" in html
    assert "Konfiguration gespeichert" not in html
    assert "Gespeichert" in html
