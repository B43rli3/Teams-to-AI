"""Erkennung von Benutzerabsichten für Antwortformate."""

from __future__ import annotations

import re

_PDF_REQUEST_RE = re.compile(
    r"\b("
    r"pdf|"
    r"als\s+pdf|"
    r"pdf[\s-]?datei|"
    r"pdf[\s-]?erstell|"
    r"pdf[\s-]?export|"
    r"pdf[\s-]?generier|"
    r"pdf[\s-]?anhang|"
    r"pdf[\s-]?schick|"
    r"pdf[\s-]?send|"
    r"als\s+datei\s+schick|"
    r"dokument\s+erstell"
    r")\b",
    re.IGNORECASE,
)


def wants_pdf_attachment(text: str) -> bool:
    """Erkennt, ob der Benutzer eine PDF-Antwort wünscht."""
    if not text or not text.strip():
        return False
    return _PDF_REQUEST_RE.search(text) is not None
