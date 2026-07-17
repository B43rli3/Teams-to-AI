"""Hilfsfunktionen fuer Konfigurationswerte und .env-Schreiben."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, TeamsTargetMode, TriggerMode
from app.teams_targets import resolve_teams_targets

EDITABLE_ENV_KEYS = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "TEAMS_TARGET_MODE",
    "TEAMS_CHAT_ID",
    "TEAMS_TEAM_ID",
    "TEAMS_CHANNEL_ID",
    "TEAMS_CHANNELS",
    "TEAMS_CHAT_IDS",
    "TRIGGER_MODE",
    "BOT_PREFIX",
    "OLLAMA_VISION_MODEL",
)


@dataclass
class EditableSettings:
    """Teilmenge der per UI bearbeitbaren Einstellungen."""

    azure_tenant_id: str
    azure_client_id: str
    teams_target_mode: str
    teams_chat_id: str
    teams_team_id: str
    teams_channel_id: str
    teams_channels: str
    teams_chat_ids: str
    trigger_mode: str
    bot_prefix: str
    ollama_vision_model: str

    @classmethod
    def from_settings(cls, settings: Settings) -> EditableSettings:
        return cls(
            azure_tenant_id=settings.azure_tenant_id,
            azure_client_id=settings.azure_client_id,
            teams_target_mode=settings.teams_target_mode.value,
            teams_chat_id=settings.teams_chat_id,
            teams_team_id=settings.teams_team_id,
            teams_channel_id=settings.teams_channel_id,
            teams_channels=settings.teams_channels,
            teams_chat_ids=settings.teams_chat_ids,
            trigger_mode=settings.trigger_mode.value,
            bot_prefix=settings.bot_prefix,
            ollama_vision_model=settings.ollama_vision_model,
        )

    def to_env_map(self) -> dict[str, str]:
        return {
            "AZURE_TENANT_ID": self.azure_tenant_id,
            "AZURE_CLIENT_ID": self.azure_client_id,
            "TEAMS_TARGET_MODE": self.teams_target_mode,
            "TEAMS_CHAT_ID": self.teams_chat_id,
            "TEAMS_TEAM_ID": self.teams_team_id,
            "TEAMS_CHANNEL_ID": self.teams_channel_id,
            "TEAMS_CHANNELS": self.teams_channels,
            "TEAMS_CHAT_IDS": self.teams_chat_ids,
            "TRIGGER_MODE": self.trigger_mode,
            "BOT_PREFIX": self.bot_prefix,
            "OLLAMA_VISION_MODEL": self.ollama_vision_model,
        }


def build_settings_from_form(
    *,
    current_settings: Settings,
    azure_tenant_id: str,
    azure_client_id: str,
    teams_target_mode: str,
    teams_chat_id: str,
    teams_team_id: str,
    teams_channel_id: str,
    teams_channels: str,
    teams_chat_ids: str,
    trigger_mode: str,
    bot_prefix: str,
    ollama_vision_model: str,
) -> EditableSettings:
    """Validiert und normalisiert UI-Eingaben."""
    normalized_target_mode = TeamsTargetMode(teams_target_mode.strip().lower())
    normalized_trigger_mode = TriggerMode(trigger_mode.strip().lower())
    prefix = bot_prefix.strip() or current_settings.bot_prefix

    if normalized_trigger_mode == TriggerMode.PREFIX and not prefix:
        raise ValueError("BOT_PREFIX darf im Prefix-Modus nicht leer sein.")

    # Frühe Validierung der Multi-Target-Syntax
    resolve_teams_targets(
        teams_channels=teams_channels,
        teams_chat_ids=teams_chat_ids,
        teams_target_mode=normalized_target_mode,
        teams_team_id=teams_team_id,
        teams_channel_id=teams_channel_id,
        teams_chat_id=teams_chat_id,
    )

    return EditableSettings(
        azure_tenant_id=azure_tenant_id.strip(),
        azure_client_id=azure_client_id.strip(),
        teams_target_mode=normalized_target_mode.value,
        teams_chat_id=teams_chat_id.strip(),
        teams_team_id=teams_team_id.strip(),
        teams_channel_id=teams_channel_id.strip(),
        teams_channels=teams_channels.strip(),
        teams_chat_ids=teams_chat_ids.strip(),
        trigger_mode=normalized_trigger_mode.value,
        bot_prefix=prefix,
        ollama_vision_model=ollama_vision_model.strip(),
    )


def update_env_file(env_path: Path, values: dict[str, str]) -> None:
    """Aktualisiert ausgewaehlte .env-Werte und erhaelt sonstige Eintraege."""
    existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated_lines: list[str] = []
    remaining = OrderedDict(values)

    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue

        key, _, _ = line.partition("=")
        clean_key = key.strip()
        if clean_key in remaining:
            updated_lines.append(f"{clean_key}={remaining.pop(clean_key)}")
        else:
            updated_lines.append(line)

    if remaining:
        if updated_lines and updated_lines[-1] != "":
            updated_lines.append("")
        for key, value in remaining.items():
            updated_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
