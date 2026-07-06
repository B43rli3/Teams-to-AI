"""CLI-Befehle für die Teams Local LLM Anwendung."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from app import __version__
from app.auth import AuthService
from app.config import Settings, get_settings
from app.graph_client import GraphClient
from app.llm_client import OllamaClient
from app.logging_config import configure_logging, get_logger
from app.message_parser import MessageParser
from app.repository import Repository

logger = get_logger(__name__)


def _create_auth_service(settings: Settings) -> AuthService:
    settings.validate_for_runtime(require_teams=False)
    settings.ensure_data_dir()
    return AuthService(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        scopes=settings.graph_scope_list,
        cache_path=settings.token_cache_path_obj,
    )


def _create_graph_client(
    auth: AuthService,
    settings: Settings,
    scopes: list[str] | None = None,
) -> tuple[GraphClient, str]:
    token_holder: dict[str, str] = {"token": ""}

    def get_token() -> str:
        if not token_holder["token"]:
            token_holder["token"] = auth.get_access_token()
        return token_holder["token"]

    def refresh_token() -> str:
        token_holder["token"] = auth.get_access_token(force_interactive=False)
        if not token_holder["token"]:
            token_holder["token"] = auth.get_access_token(force_interactive=True)
        return token_holder["token"]

    if scopes:
        auth_discovery = AuthService(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            scopes=scopes,
            cache_path=settings.token_cache_path_obj,
        )
        token_holder["token"] = auth_discovery.get_access_token()

        def get_token_discovery() -> str:
            return token_holder["token"]

        def refresh_token_discovery() -> str:
            token_holder["token"] = auth_discovery.get_access_token()
            return token_holder["token"]

        client = GraphClient(
            token_provider=get_token_discovery,
            token_refresher=refresh_token_discovery,
            max_retries=settings.http_max_retries,
            retry_base_seconds=settings.http_retry_base_seconds,
        )
    else:
        client = GraphClient(
            token_provider=get_token,
            token_refresher=refresh_token,
            max_retries=settings.http_max_retries,
            retry_base_seconds=settings.http_retry_base_seconds,
        )

    return client, token_holder["token"]


async def cmd_login(_args: argparse.Namespace) -> int:
    """Startet oder überprüft die Microsoft-Anmeldung."""
    settings = get_settings()
    configure_logging(settings.log_level)
    auth = _create_auth_service(settings)

    token = auth.get_access_token()
    if token:
        print("Anmeldung erfolgreich (Token aus Cache oder interaktiv).")
        auth.save_cache()
        return 0
    print("Anmeldung fehlgeschlagen.")
    return 1


async def cmd_whoami(_args: argparse.Namespace) -> int:
    """Zeigt den angemeldeten Benutzer an."""
    settings = get_settings()
    configure_logging(settings.log_level)
    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings)

    await client.start()
    try:
        me = await client.get_me()
        print(f"Anzeigename: {me.get('displayName', 'Unbekannt')}")
        print(f"Benutzer-ID:  {me.get('id', 'Unbekannt')}")
        print(f"E-Mail:       {me.get('mail', me.get('userPrincipalName', 'Unbekannt'))}")
        return 0
    finally:
        await client.close()
        auth.save_cache()


async def cmd_discover_teams(_args: argparse.Namespace) -> int:
    """Listet Teams des Benutzers auf."""
    settings = get_settings()
    configure_logging(settings.log_level)
    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings, scopes=settings.discovery_scopes)

    await client.start()
    try:
        teams = await client.get_joined_teams()
        if not teams:
            print("Keine Teams gefunden.")
            return 0

        print(f"\n{'Name':<40} {'ID'}")
        print("-" * 80)
        for team in teams:
            name = str(team.get("displayName", "Unbekannt"))
            team_id = str(team.get("id", ""))
            print(f"{name:<40} {team_id}")
        return 0
    finally:
        await client.close()
        auth.save_cache()


async def cmd_discover_channels(args: argparse.Namespace) -> int:
    """Listet Kanäle eines Teams auf."""
    settings = get_settings()
    configure_logging(settings.log_level)
    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings, scopes=settings.discovery_scopes)

    team_id = args.team_id
    if not team_id:
        print("Fehler: --team-id ist erforderlich.")
        return 1

    await client.start()
    try:
        channels = await client.get_team_channels(team_id)
        if not channels:
            print("Keine Kanäle gefunden.")
            return 0

        print(f"\n{'Name':<30} {'Typ':<15} {'ID'}")
        print("-" * 90)
        for channel in channels:
            name = str(channel.get("displayName", "Unbekannt"))
            membership = str(channel.get("membershipType", "standard"))
            channel_id = str(channel.get("id", ""))
            print(f"{name:<30} {membership:<15} {channel_id}")
        return 0
    finally:
        await client.close()
        auth.save_cache()


async def cmd_test_graph(_args: argparse.Namespace) -> int:
    """Prüft Graph-Zugriff auf den konfigurierten Kanal."""
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_for_runtime()
    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings)

    await client.start()
    try:
        messages = await client.get_channel_messages(
            settings.teams_team_id,
            settings.teams_channel_id,
            top=5,
        )
        print(f"Graph-Verbindung OK. {len(messages)} Nachrichten abgerufen (max. 5).")
        for msg in messages[:3]:
            msg_id = str(msg.get("id", ""))[:12]
            created = str(msg.get("createdDateTime", ""))
            msg_type = str(msg.get("messageType", ""))
            print(f"  - {msg_id}… | {created} | {msg_type}")
        return 0
    except Exception as exc:
        print(f"Graph-Test fehlgeschlagen: {exc}")
        return 1
    finally:
        await client.close()
        auth.save_cache()


async def cmd_test_ollama(_args: argparse.Namespace) -> int:
    """Sendet eine Testanfrage an Ollama."""
    settings = get_settings()
    configure_logging(settings.log_level)

    ollama = OllamaClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
    )
    await ollama.start()
    try:
        healthy = await ollama.health_check()
        if not healthy:
            print("Ollama ist nicht erreichbar.")
            return 1

        models = await ollama.list_models()
        print(f"Ollama erreichbar. Installierte Modelle: {', '.join(models)}")

        if settings.ollama_model not in models:
            print(f"Warnung: Modell '{settings.ollama_model}' nicht gefunden.")

        response = await ollama.chat(
            [{"role": "user", "content": "Antworte nur mit: OK"}],
            system_prompt="Antworte kurz.",
        )
        print(f"Testantwort: {response[:100]}")
        return 0
    except Exception as exc:
        print(f"Ollama-Test fehlgeschlagen: {exc}")
        return 1
    finally:
        await ollama.close()


async def cmd_send_test_reply(args: argparse.Namespace) -> int:
    """Postet eine Testantwort unter eine vorhandene Nachricht."""
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_for_runtime()

    message_id = args.message_id
    if not message_id:
        print("Fehler: --message-id ist erforderlich.")
        return 1

    confirm = input(
        f"Möchten Sie wirklich eine Testantwort unter Nachricht {message_id} senden? [j/N]: "
    )
    if confirm.lower() not in ("j", "ja", "y", "yes"):
        print("Abgebrochen.")
        return 0

    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings)
    parser = MessageParser(max_response_characters=settings.llm_max_response_characters)

    await client.start()
    try:
        html = parser.format_llm_response_for_teams(
            "Dies ist eine Testantwort vom Teams Local LLM Assistenten."
        )
        result = await client.send_reply(
            settings.teams_team_id,
            settings.teams_channel_id,
            message_id,
            html,
        )
        print(f"Testantwort gesendet. Reply-ID: {result.get('id', 'Unbekannt')}")
        return 0
    except Exception as exc:
        print(f"Fehler beim Senden: {exc}")
        return 1
    finally:
        await client.close()
        auth.save_cache()


async def cmd_reset_watermark(_args: argparse.Namespace) -> int:
    """Setzt den Polling-Startpunkt zurück."""
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_data_dir()

    confirm = input(
        "WARNUNG: Dies setzt den Polling-Startpunkt zurück. "
        "Beim nächsten Start werden vorhandene Nachrichten erneut als 'gesehen' markiert "
        "oder bei PROCESS_BACKLOG=true verarbeitet.\n"
        "Fortfahren? [j/N]: "
    )
    if confirm.lower() not in ("j", "ja", "y", "yes"):
        print("Abgebrochen.")
        return 0

    repo = Repository(settings.database_path)
    await repo.connect()
    try:
        await repo.reset_watermark()
        print("Polling-Startpunkt wurde zurückgesetzt.")
        return 0
    finally:
        await repo.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Teams Local LLM - CLI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("login", help="Microsoft-Anmeldung starten oder prüfen")
    subparsers.add_parser("whoami", help="Angemeldeten Benutzer anzeigen")
    subparsers.add_parser("discover-teams", help="Teams des Benutzers auflisten")

    channels_parser = subparsers.add_parser(
        "discover-channels", help="Kanäle eines Teams auflisten"
    )
    channels_parser.add_argument("--team-id", required=True, help="Team-ID")

    subparsers.add_parser("test-graph", help="Graph-Verbindung testen")
    subparsers.add_parser("test-ollama", help="Ollama-Verbindung testen")

    reply_parser = subparsers.add_parser(
        "send-test-reply", help="Testantwort unter eine Nachricht senden"
    )
    reply_parser.add_argument("--message-id", required=True, help="Nachrichten-ID")

    subparsers.add_parser("reset-watermark", help="Polling-Startpunkt zurücksetzen")

    return parser


def main() -> None:
    """Haupteinstiegspunkt der CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands: dict[str, Any] = {
        "login": cmd_login,
        "whoami": cmd_whoami,
        "discover-teams": cmd_discover_teams,
        "discover-channels": cmd_discover_channels,
        "test-graph": cmd_test_graph,
        "test-ollama": cmd_test_ollama,
        "send-test-reply": cmd_send_test_reply,
        "reset-watermark": cmd_reset_watermark,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    exit_code = asyncio.run(handler(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
