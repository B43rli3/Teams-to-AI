"""Erkennung von Benutzerabsichten für Antwortformate."""

from __future__ import annotations

import re

# Nur klare Aufforderungen, eine PDF *zu erzeugen/zu senden*.
# Reine Erwähnungen („Was steht in der PDF?“) sollen NICHT matchen.
_PDF_REQUEST_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"\bals\s+pdf\b|"
    r"\bpdf[\s-]?erstell\w*|"
    r"\bpdf[\s-]?export\w*|"
    r"\bpdf[\s-]?generier\w*|"
    r"\bpdf[\s-]?erzeug\w*|"
    r"\bpdf[\s-]?schick\w*|"
    r"\bpdf[\s-]?send\w*|"
    r"\bsende?\s+(?:mir\s+)?(?:das\s+)?als\s+pdf\b|"
    r"\bschick\w*\s+(?:mir\s+)?(?:das\s+)?als\s+pdf\b|"
    r"\berstell\w*\s+(?:mir\s+)?(?:eine?\s+)?pdf\b|"
    r"\bals\s+datei\s+schick\w*|"
    r"\bdokument\s+erstell\w*\s+als\s+pdf\b"
    r")"
)


def wants_pdf_attachment(text: str) -> bool:
    """Erkennt, ob der Benutzer eine PDF-Antwort wünscht."""
    if not text or not text.strip():
        return False
    # Anhangskontext abschneiden: nur den Nutzertext vor Dokumentblöcken prüfen
    user_part = text.split("\n\n--- Dokument:", 1)[0]
    user_part = user_part.split("\n\n[Es wurden", 1)[0]
    user_part = user_part.split("\n\n[Anhang", 1)[0]
    return _PDF_REQUEST_RE.search(user_part) is not None
