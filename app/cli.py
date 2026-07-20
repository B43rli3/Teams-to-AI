"""CLI-Befehle für die Teams Local LLM Anwendung."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from app import __version__
from app.auth import AuthService
from app.config import Settings, get_settings
from app.exceptions import GraphAPIError
from app.file_sharing import (
    build_invite_recipient_payloads,
    count_cross_tenant_recipients,
    parse_chat_member_recipients,
)
from app.graph_client import GraphClient
from app.llm_client import OllamaClient
from app.logging_config import configure_logging, get_logger
from app.message_parser import MessageParser
from app.mcp_client import McpHttpClient
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


async def cmd_discover_chats(_args: argparse.Namespace) -> int:
    """Listet Chats des Benutzers auf."""
    settings = get_settings()
    configure_logging(settings.log_level)
    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings, scopes=settings.discovery_scopes)

    await client.start()
    try:
        chats = await client.get_joined_chats()
        if not chats:
            print("Keine Chats gefunden.")
            return 0

        print(f"\n{'Typ':<12} {'Thema/Name':<35} {'ID'}")
        print("-" * 100)
        for chat in chats:
            chat_type = str(chat.get("chatType", "unknown"))
            topic = str(chat.get("topic") or "(ohne Thema)")
            chat_id = str(chat.get("id", ""))
            print(f"{chat_type:<12} {topic[:35]:<35} {chat_id}")
        return 0
    finally:
        await client.close()
        auth.save_cache()


async def cmd_test_graph(_args: argparse.Namespace) -> int:
    """Prüft Graph-Zugriff auf die konfigurierten Ziele."""
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_for_runtime()
    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings)

    await client.start()
    try:
        targets = settings.resolved_targets
        if not targets:
            print("Keine Ziele konfiguriert.")
            return 1

        for target in targets:
            if target.kind.value == "chat":
                messages = await client.get_chat_messages(target.chat_id, top=5)
                label = f"Chat {target.chat_id[:40]}…"
            else:
                messages = await client.get_channel_messages(
                    target.team_id,
                    target.channel_id,
                    top=5,
                )
                label = f"Kanal {target.channel_id[:30]}…"
            print(
                f"Graph-Verbindung OK ({label}). "
                f"{len(messages)} Nachrichten abgerufen (max. 5)."
            )
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
        if settings.is_chat_mode:
            result = await client.send_chat_reply(
                settings.teams_chat_id,
                message_id,
                html,
            )
        else:
            result = await client.send_channel_reply(
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


async def cmd_test_pdf_share(args: argparse.Namespace) -> int:
    """Prüft, ob PDF-Freigaben (inkl. Cross-Tenant per E-Mail) funktionieren."""
    settings = get_settings()
    configure_logging(settings.log_level)
    auth = _create_auth_service(settings)
    client, _ = _create_graph_client(auth, settings)

    email = (args.email or "").strip()
    chat_id = (args.chat_id or "").strip()
    if not email and not chat_id:
        print("Fehler: Mindestens --email oder --chat-id angeben.")
        return 1

    await client.start()
    try:
        me = await client.get_me()
        bot_user_id = str(me.get("id") or "")
        bot_name = str(me.get("displayName") or "Unbekannt")
        bot_mail = str(me.get("mail") or me.get("userPrincipalName") or "")

        bot_tenant_id = ""
        try:
            bot_tenant_id = await client.get_organization_tenant_id()
        except GraphAPIError:
            bot_tenant_id = ""

        print("=== Bot-Konto ===")
        print(f"Name:   {bot_name}")
        print(f"E-Mail: {bot_mail}")
        print(f"Tenant: {bot_tenant_id or '(nicht ermittelbar – ok für Chat-Test)'}")

        pdf_bytes = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        pdf_bytes += b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
        pdf_bytes += b"trailer<</Root 1 0 R>>\n%%EOF\n"
        filename = "freigabe-test.pdf"

        print("\n=== Upload ===")
        drive_item = await client.upload_file_to_teams_chat_files_folder(
            filename=filename,
            content=pdf_bytes,
            content_type="application/pdf",
        )
        web_url = str(drive_item.get("webUrl") or "")
        print(f"OK: {filename}")
        if web_url:
            print(f"URL: {web_url}")

        recipients = []
        if chat_id:
            print("\n=== Chat-Mitglieder ===")
            members = await client.get_chat_members(chat_id)
            recipients = parse_chat_member_recipients(
                members,
                exclude_user_id=bot_user_id,
            )
            if not bot_tenant_id:
                for member in members:
                    member_user_id = str(member.get("userId") or "")
                    if member_user_id == bot_user_id:
                        bot_tenant_id = str(member.get("tenantId") or "")
                        break
            if not recipients:
                print("Keine weiteren Chat-Mitglieder gefunden.")
            for recipient in recipients:
                scope = "intern"
                if bot_tenant_id and recipient.tenant_id:
                    scope = "EXTERN" if recipient.tenant_id != bot_tenant_id else "intern"
                print(
                    f"- {recipient.display_name or recipient.user_id}: "
                    f"{recipient.email or '(keine E-Mail)'} [{scope}]"
                )

        if email:
            recipients.append(
                parse_chat_member_recipients(
                    [{"userId": "manual", "email": email, "displayName": email}]
                )[0]
            )

        invite_payloads = build_invite_recipient_payloads(recipients)
        cross_tenant = count_cross_tenant_recipients(recipients, bot_tenant_id)

        print("\n=== Freigabe per invite (E-Mail/objectId) ===")
        if not invite_payloads:
            print("Keine Empfänger für Freigabe vorhanden.")
        else:
            try:
                granted = await client.invite_recipients_to_drive_item(
                    drive_item,
                    recipients=invite_payloads,
                )
                print(f"OK: {granted} Empfänger freigegeben.")
                if cross_tenant:
                    print(
                        "Hinweis: Cross-Tenant erkannt – Freigabe erfolgte per E-Mail "
                        "(falls vorhanden)."
                    )
            except GraphAPIError as exc:
                print(f"FEHLER: {exc}")
                print(
                    "→ IT prüfen: Externes Teilen in SharePoint/OneDrive (Stratest) "
                    "für den Bot-Account erlauben."
                )
                return 1

        print("\n=== Organisations-Link (nur intern) ===")
        if cross_tenant:
            print("Übersprungen (Cross-Tenant – Org-Link hilft externen Nutzern nicht).")
        else:
            try:
                org_url = await client.create_organization_view_link(drive_item)
                if org_url:
                    print(f"OK: {org_url}")
                else:
                    print("Kein Link erhalten.")
            except GraphAPIError as exc:
                print(f"FEHLER: {exc}")

        print("\n=== Ergebnis ===")
        print(
            "Wenn 'invite OK' erscheint, öffne die URL oben im Browser "
            "(mit deinem Stranext-Konto angemeldet)."
        )
        print(
            "Funktioniert der Download dort, kann der Bot dir PDFs per Teams senden."
        )
        return 0
    finally:
        await client.close()
        auth.save_cache()


async def cmd_test_cpd_mcp(args: argparse.Namespace) -> int:
    """Testet die Verbindung zum CPD-AutoPlan MCP-Server."""
    settings = get_settings()
    configure_logging(settings.log_level)

    url = settings.cpd_mcp_url.strip()
    token = settings.cpd_mcp_token.strip()
    if not url:
        print("Fehler: CPD_MCP_URL ist nicht gesetzt.")
        return 1
    if not token:
        print(
            "Fehler: CPD_MCP_TOKEN ist nicht gesetzt "
            "(Token aus dem CPD-Agent-Panel kopieren)."
        )
        return 1

    client = McpHttpClient(
        base_url=url,
        token=token,
        timeout_seconds=float(settings.cpd_mcp_timeout_seconds),
    )
    await client.start()
    try:
        tools = await client.list_tools()
        print(f"CPD MCP verbunden: {url}")
        print(f"Tools ({len(tools)}):")
        for tool in sorted(tools, key=lambda item: str(item.get("name") or "")):
            name = str(tool.get("name") or "?")
            description = str(tool.get("description") or "").strip().replace("\n", " ")
            if len(description) > 120:
                description = description[:117] + "..."
            line = f"  - {name}"
            if description:
                line += f": {description}"
            print(line)

        if args.call_get_state:
            result = await client.call_tool("get_state", {})
            print("\nget_state (Auszug):")
            print(result[:2000])
            from app.mcp_client import format_cpd_error_message, parse_cpd_tool_payload

            payload = parse_cpd_tool_payload(result)
            if payload is not None and payload.get("ok") is False:
                print("\nHinweis:", format_cpd_error_message(payload))
        else:
            print(
                "\nHinweis: Nur tools/list ausgeführt. "
                "Für einen read-only Test: --call-get-state"
            )
        return 0
    except Exception as exc:
        print(f"CPD MCP Fehler: {exc}")
        return 1
    finally:
        await client.close()


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

    subparsers.add_parser("discover-chats", help="Gruppen- und 1:1-Chats auflisten")

    subparsers.add_parser("test-graph", help="Graph-Verbindung testen")
    subparsers.add_parser("test-ollama", help="Ollama-Verbindung testen")

    share_parser = subparsers.add_parser(
        "test-pdf-share",
        help="PDF-Freigabe testen (Cross-Tenant per E-Mail)",
    )
    share_parser.add_argument(
        "--email",
        help="E-Mail-Adresse eines externen Empfängers (z. B. dein Stranext-Konto)",
    )
    share_parser.add_argument(
        "--chat-id",
        help="Chat-ID – testet Freigabe an alle Chat-Mitglieder",
    )

    cpd_parser = subparsers.add_parser(
        "test-cpd-mcp",
        help="CPD-AutoPlan MCP testen (Tools auflisten, optional get_state)",
    )
    cpd_parser.add_argument(
        "--call-get-state",
        action="store_true",
        help="Ruft zusätzlich das read-only Tool get_state auf",
    )

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
        "discover-chats": cmd_discover_chats,
        "test-graph": cmd_test_graph,
        "test-ollama": cmd_test_ollama,
        "test-pdf-share": cmd_test_pdf_share,
        "test-cpd-mcp": cmd_test_cpd_mcp,
        "send-test-reply": cmd_send_test_reply,
        "reset-watermark": cmd_reset_watermark,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    try:
        exit_code = asyncio.run(handler(args))
    except KeyboardInterrupt:
        # Bei Ctrl+C/Schließen wird sonst häufig eine Stacktrace angezeigt.
        # Der Token-Cache wird ggf. bereits vorher gespeichert.
        print("Abgebrochen.")
        sys.exit(130)
    except asyncio.CancelledError:
        print("Abgebrochen.")
        sys.exit(130)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
