"""Benutzerdefinierte Ausnahmen für die Anwendung."""

from __future__ import annotations


class TeamsLocalLLMError(Exception):
    """Basisklasse für Anwendungsfehler."""


class ConfigurationError(TeamsLocalLLMError):
    """Ungültige oder unvollständige Konfiguration."""


class AuthenticationError(TeamsLocalLLMError):
    """Fehler bei der Microsoft-Authentifizierung."""


class GraphAPIError(TeamsLocalLLMError):
    """Fehler bei Microsoft Graph API-Aufrufen."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after = retry_after


class GraphPermissionError(GraphAPIError):
    """Fehlende Graph-Berechtigungen (HTTP 403)."""


class OllamaError(TeamsLocalLLMError):
    """Fehler bei Ollama API-Aufrufen."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MessageProcessingError(TeamsLocalLLMError):
    """Fehler bei der Nachrichtenverarbeitung."""


class DatabaseError(TeamsLocalLLMError):
    """Fehler bei Datenbankoperationen."""
