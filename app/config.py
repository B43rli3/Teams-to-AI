"""Anwendungskonfiguration über Umgebungsvariablen und .env-Datei."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.exceptions import ConfigurationError


class TriggerMode(StrEnum):
    ALL = "all"
    PREFIX = "prefix"
    MENTION = "mention"


class TeamsTargetMode(StrEnum):
    CHANNEL = "channel"
    CHAT = "chat"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8080
    log_level: str = "INFO"

    azure_tenant_id: str = ""
    azure_client_id: str = ""

    # offline_access wird von MSAL automatisch hinzugefügt und darf nicht manuell übergeben werden.
    # Channel-Modus: User.Read,ChannelMessage.Read.All,ChannelMessage.Send
    # Chat-Modus:   User.Read,Chat.Read,Chat.ReadWrite
    graph_scopes: str = "User.Read,ChannelMessage.Read.All,ChannelMessage.Send"

    # channel = Team-Kanal | chat = Gruppen-/1:1-Chat
    teams_target_mode: TeamsTargetMode = TeamsTargetMode.CHANNEL
    teams_team_id: str = ""
    teams_channel_id: str = ""
    teams_chat_id: str = ""

    poll_interval_seconds: int = 10
    poll_page_size: int = 20
    process_backlog: bool = False
    backlog_limit: int = 5

    trigger_mode: TriggerMode = TriggerMode.ALL
    bot_prefix: str = "/ai"
    bot_mention_id: str = ""

    process_thread_replies: bool = False

    process_attachments: bool = True
    process_images: bool = True
    process_documents: bool = True
    attachment_max_files: int = 5
    attachment_max_bytes: int = 10_000_000
    attachment_max_document_chars: int = 30000

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:14b"
    # Optional: separates Vision-Modell fuer Bilder (z. B. qwen2.5vl:7b, llava)
    ollama_vision_model: str = ""
    ollama_timeout_seconds: int = 180
    ollama_keep_alive: str = "10m"

    llm_system_prompt: str = (
        "Du bist ein hilfreicher interner Assistent in Microsoft Teams. "
        "Antworte präzise, sachlich und auf Deutsch."
    )
    llm_max_context_messages: int = 10
    llm_max_response_characters: int = 12000
    llm_max_concurrency: int = 1

    http_max_retries: int = 4
    http_retry_base_seconds: float = 2.0

    database_path: str = "data/teams_llm.db"
    token_cache_path: str = "data/msal_token_cache.json"

    @field_validator("trigger_mode", mode="before")
    @classmethod
    def parse_trigger_mode(cls, value: object) -> TriggerMode:
        if isinstance(value, TriggerMode):
            return value
        if isinstance(value, str):
            return TriggerMode(value.lower())
        raise ValueError(f"Ungültiger TRIGGER_MODE: {value}")

    @field_validator("teams_target_mode", mode="before")
    @classmethod
    def parse_target_mode(cls, value: object) -> TeamsTargetMode:
        if isinstance(value, TeamsTargetMode):
            return value
        if isinstance(value, str):
            return TeamsTargetMode(value.lower())
        raise ValueError(f"Ungültiger TEAMS_TARGET_MODE: {value}")

    @property
    def graph_scope_list(self) -> list[str]:
        return [s.strip() for s in self.graph_scopes.split(",") if s.strip()]

    @property
    def discovery_scopes(self) -> list[str]:
        base = set(self.graph_scope_list)
        if self.teams_target_mode == TeamsTargetMode.CHAT:
            base.update(["Chat.Read", "Chat.ReadBasic"])
        else:
            base.update(["Team.ReadBasic.All", "Channel.ReadBasic.All"])
        return sorted(base)

    @property
    def is_chat_mode(self) -> bool:
        return self.teams_target_mode == TeamsTargetMode.CHAT

    @property
    def database_path_obj(self) -> Path:
        return Path(self.database_path)

    @property
    def token_cache_path_obj(self) -> Path:
        return Path(self.token_cache_path)

    @property
    def data_dir(self) -> Path:
        return self.database_path_obj.parent

    def validate_for_runtime(self, *, require_teams: bool = True) -> None:
        """Validiert die Konfiguration und wirft ConfigurationError bei Fehlern."""
        errors: list[str] = []

        if not self.azure_tenant_id:
            errors.append("AZURE_TENANT_ID ist nicht gesetzt.")
        if not self.azure_client_id:
            errors.append("AZURE_CLIENT_ID ist nicht gesetzt.")
        if require_teams:
            if self.teams_target_mode == TeamsTargetMode.CHAT:
                if not self.teams_chat_id:
                    errors.append(
                        "TEAMS_CHAT_ID ist nicht gesetzt "
                        "(erforderlich bei TEAMS_TARGET_MODE=chat)."
                    )
            else:
                if not self.teams_team_id:
                    errors.append("TEAMS_TEAM_ID ist nicht gesetzt.")
                if not self.teams_channel_id:
                    errors.append("TEAMS_CHANNEL_ID ist nicht gesetzt.")
        if self.poll_interval_seconds < 1:
            errors.append("POLL_INTERVAL_SECONDS muss mindestens 1 sein.")
        if self.poll_page_size < 1:
            errors.append("POLL_PAGE_SIZE muss mindestens 1 sein.")
        if self.backlog_limit < 1:
            errors.append("BACKLOG_LIMIT muss mindestens 1 sein.")
        if self.llm_max_concurrency < 1:
            errors.append("LLM_MAX_CONCURRENCY muss mindestens 1 sein.")
        if self.trigger_mode == TriggerMode.MENTION and not self.bot_mention_id:
            errors.append(
                "BOT_MENTION_ID ist erforderlich, wenn TRIGGER_MODE=mention gesetzt ist."
            )

        if errors:
            raise ConfigurationError(
                "Konfigurationsfehler:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    def ensure_data_dir(self) -> None:
        """Erstellt den Datenordner, falls er nicht existiert."""
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Lädt und gibt die Anwendungseinstellungen zurück."""
    return Settings()
