"""MSAL-Authentifizierung mit Device-Code-Flow und lokalem Token-Cache."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, cast

import msal

from app.exceptions import AuthenticationError
from app.logging_config import get_logger

logger = get_logger(__name__)

GRAPH_AUTHORITY_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}"

# MSAL fuegt diese OpenID-Scopes selbst hinzu - sie duerfen nicht manuell uebergeben werden.
_MSAL_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})


def normalize_msal_scopes(scopes: list[str]) -> list[str]:
    """Entfernt MSAL-reservierte Scopes und normalisiert Graph-Berechtigungen."""
    normalized: list[str] = []
    seen: set[str] = set()

    for scope in scopes:
        cleaned = scope.strip()
        if not cleaned or cleaned in _MSAL_RESERVED_SCOPES:
            continue
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            value = cleaned
        else:
            # Kurzform → vollständige Graph-API-Scope-URI
            value = f"https://graph.microsoft.com/{cleaned}"
        if value not in seen:
            seen.add(value)
            normalized.append(value)

    return normalized


class TokenCacheManager:
    """Verwaltet den MSAL-Token-Cache mit restriktiven Dateirechten."""

    def __init__(self, cache_path: Path) -> None:
        self._cache_path = cache_path
        self._cache = msal.SerializableTokenCache()

    @property
    def cache(self) -> msal.SerializableTokenCache:
        return self._cache

    def load(self) -> None:
        """Lädt den Token-Cache von der Festplatte."""
        if not self._cache_path.exists():
            return
        try:
            data = self._cache_path.read_text(encoding="utf-8")
            if data.strip():
                self._cache.deserialize(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("token_cache_load_failed", error=str(exc))

    def save(self) -> None:
        """Speichert den Token-Cache atomar mit restriktiven Rechten."""
        if not self._cache.has_state_changed:
            return

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._cache_path.with_suffix(".tmp")
        content = self._cache.serialize()

        try:
            temp_path.write_text(content, encoding="utf-8")
            self._set_restrictive_permissions(temp_path)
            temp_path.replace(self._cache_path)
            self._set_restrictive_permissions(self._cache_path)
            logger.debug("token_cache_saved")
        except OSError as exc:
            logger.error("token_cache_save_failed", error=str(exc))
            raise AuthenticationError(
                f"Token-Cache konnte nicht gespeichert werden: {exc}"
            ) from exc

    @staticmethod
    def _set_restrictive_permissions(path: Path) -> None:
        """Setzt restriktive Dateirechte (nur Besitzer)."""
        if sys.platform == "win32":
            return
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


class AuthService:
    """Authentifizierung über MSAL Device-Code-Flow."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        scopes: list[str],
        cache_path: Path,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._scopes = normalize_msal_scopes(scopes)
        if not self._scopes:
            raise AuthenticationError(
                "Keine gültigen Graph-Scopes konfiguriert. "
                "Bitte setzen Sie z. B. User.Read,ChannelMessage.Read.All,ChannelMessage.Send."
            )
        self._cache_manager = TokenCacheManager(cache_path)
        self._cache_manager.load()
        self._app = msal.PublicClientApplication(
            client_id=client_id,
            authority=GRAPH_AUTHORITY_TEMPLATE.format(tenant_id=tenant_id),
            token_cache=self._cache_manager.cache,
        )

    @property
    def cache_manager(self) -> TokenCacheManager:
        return self._cache_manager

    def get_accounts(self) -> list[dict[str, Any]]:
        """Gibt alle bekannten MSAL-Konten zurück."""
        return cast(list[dict[str, Any]], self._app.get_accounts())

    def acquire_token_silent(self) -> dict[str, Any] | None:
        """Versucht eine stille Token-Erneuerung."""
        accounts = self.get_accounts()
        if not accounts:
            return None

        result = self._app.acquire_token_silent(
            scopes=self._scopes,
            account=accounts[0],
        )
        if result and "access_token" in result:
            self._cache_manager.save()
            return cast(dict[str, Any], result)
        return None

    def acquire_token_interactive(self) -> dict[str, Any]:
        """Startet den Device-Code-Flow für interaktive Anmeldung."""
        flow = self._app.initiate_device_flow(scopes=self._scopes)
        if "user_code" not in flow:
            error_desc = flow.get("error_description", "Unbekannter Fehler")
            raise AuthenticationError(
                f"Device-Code-Flow konnte nicht gestartet werden: {error_desc}"
            )

        print("\n" + "=" * 60)
        print("Microsoft-Anmeldung erforderlich")
        print("=" * 60)
        print(f"\n{flow['message']}\n")
        print("Öffnen Sie die URL in Ihrem Browser und geben Sie den Code ein.")
        print("=" * 60 + "\n")

        result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            error_desc = result.get("error_description", result.get("error", "Unbekannt"))
            raise AuthenticationError(f"Anmeldung fehlgeschlagen: {error_desc}")

        self._cache_manager.save()
        logger.info("user_login_success")
        return cast(dict[str, Any], result)

    def get_access_token(self, *, force_interactive: bool = False) -> str:
        """Gibt ein gültiges Access Token zurück."""
        if not force_interactive:
            silent_result = self.acquire_token_silent()
            if silent_result and "access_token" in silent_result:
                return str(silent_result["access_token"])

        interactive_result = self.acquire_token_interactive()
        return str(interactive_result["access_token"])

    def save_cache(self) -> None:
        """Speichert den Token-Cache."""
        self._cache_manager.save()

    def clear_cache(self) -> None:
        """Löscht den Token-Cache."""
        cache_path = self._cache_manager._cache_path
        if cache_path.exists():
            cache_path.unlink()
        self._cache_manager._cache = msal.SerializableTokenCache()
        self._app = msal.PublicClientApplication(
            client_id=self._client_id,
            authority=GRAPH_AUTHORITY_TEMPLATE.format(tenant_id=self._tenant_id),
            token_cache=self._cache_manager.cache,
        )
