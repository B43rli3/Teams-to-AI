"""Auflösung von Teams-Überwachungszielen (Kanäle und Chats)."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import TeamsTargetMode


@dataclass(frozen=True)
class TeamsTarget:
    """Ein einzelnes Überwachungsziel (Kanal oder Chat)."""

    kind: TeamsTargetMode
    team_id: str = ""
    channel_id: str = ""
    chat_id: str = ""

    @property
    def key(self) -> str:
        if self.kind == TeamsTargetMode.CHAT:
            return f"chat:{self.chat_id}"
        return f"channel:{self.team_id}/{self.channel_id}"

    @property
    def label(self) -> str:
        if self.kind == TeamsTargetMode.CHAT:
            return f"Chat {self.chat_id}"
        return f"Kanal {self.channel_id} (Team {self.team_id})"

    def validate(self) -> None:
        if self.kind == TeamsTargetMode.CHAT:
            if not self.chat_id.strip():
                raise ValueError("Chat-Ziel ohne TEAMS_CHAT_ID / chat_id.")
            return
        if not self.team_id.strip() or not self.channel_id.strip():
            raise ValueError("Kanal-Ziel benötigt team_id und channel_id.")


def parse_channel_entries(raw: str) -> list[TeamsTarget]:
    """Parst TEAMS_CHANNELS=teamId|channelId,teamId|channelId."""
    targets: list[TeamsTarget] = []
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        if "|" not in entry:
            raise ValueError(
                f"Ungültiger TEAMS_CHANNELS-Eintrag '{entry}'. "
                "Erwartet: teamId|channelId"
            )
        team_id, channel_id = entry.split("|", 1)
        team_id = team_id.strip()
        channel_id = channel_id.strip()
        if not team_id or not channel_id:
            raise ValueError(
                f"Ungültiger TEAMS_CHANNELS-Eintrag '{entry}'. "
                "teamId und channelId dürfen nicht leer sein."
            )
        targets.append(
            TeamsTarget(
                kind=TeamsTargetMode.CHANNEL,
                team_id=team_id,
                channel_id=channel_id,
            )
        )
    return targets


def parse_chat_ids(raw: str) -> list[TeamsTarget]:
    """Parst TEAMS_CHAT_IDS=id1,id2 oder eine einzelne Chat-ID."""
    targets: list[TeamsTarget] = []
    for part in raw.split(","):
        chat_id = part.strip()
        if not chat_id:
            continue
        targets.append(TeamsTarget(kind=TeamsTargetMode.CHAT, chat_id=chat_id))
    return targets


def resolve_teams_targets(
    *,
    teams_channels: str = "",
    teams_chat_ids: str = "",
    teams_target_mode: TeamsTargetMode = TeamsTargetMode.CHANNEL,
    teams_team_id: str = "",
    teams_channel_id: str = "",
    teams_chat_id: str = "",
) -> list[TeamsTarget]:
    """Löst Multi-Target-Listen und Legacy-Einzelwerte zu einer Zielliste auf."""
    targets: list[TeamsTarget] = []
    seen: set[str] = set()

    def _add(target: TeamsTarget) -> None:
        target.validate()
        if target.key in seen:
            return
        seen.add(target.key)
        targets.append(target)

    for target in parse_channel_entries(teams_channels):
        _add(target)
    for target in parse_chat_ids(teams_chat_ids):
        _add(target)

    # Legacy: einzelne Chat-ID zusätzlich zu TEAMS_CHAT_IDS
    if teams_chat_id.strip():
        _add(TeamsTarget(kind=TeamsTargetMode.CHAT, chat_id=teams_chat_id.strip()))

    # Legacy: einzelner Kanal
    if teams_team_id.strip() and teams_channel_id.strip():
        _add(
            TeamsTarget(
                kind=TeamsTargetMode.CHANNEL,
                team_id=teams_team_id.strip(),
                channel_id=teams_channel_id.strip(),
            )
        )

    # Wenn noch nichts gesetzt: Legacy-Modus ohne IDs → leere Liste
    # (Validierung erfolgt in Settings.validate_for_runtime)
    if not targets and teams_target_mode == TeamsTargetMode.CHAT and teams_chat_id.strip():
        _add(TeamsTarget(kind=TeamsTargetMode.CHAT, chat_id=teams_chat_id.strip()))
    if (
        not targets
        and teams_target_mode == TeamsTargetMode.CHANNEL
        and teams_team_id.strip()
        and teams_channel_id.strip()
    ):
        _add(
            TeamsTarget(
                kind=TeamsTargetMode.CHANNEL,
                team_id=teams_team_id.strip(),
                channel_id=teams_channel_id.strip(),
            )
        )

    return targets
