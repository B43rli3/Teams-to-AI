"""Erkennung von Anfragen, die CPD-Wissen benötigen."""

from __future__ import annotations

import re

# Typische Begriffe für Gebäudemodelle, Pläne und CPD-Inhalte.
_CPD_KEYWORD_RE = re.compile(
    r"(?i)\b("
    r"cpd|"
    r"bauplan|"
    r"geschossplan|"
    r"grundriss|"
    r"ifc|"
    r"bim|"
    r"modell|"
    r"modelle|"
    r"plan|"
    r"pläne|"
    r"plaene|"
    r"zeichnung|"
    r"projekt|"
    r"gebäude|"
    r"gebaeude|"
    r"raum|"
    r"räume|"
    r"raeume|"
    r"komponente|"
    r"bauelement|"
    r"leistungsphase|"
    r"ausschreibung|"
    r"quantity|"
    r"menge|"
    r"kennwert"
    r")\b"
)


def needs_cpd_context(text: str) -> bool:
    """Prüft, ob eine Teams-Anfrage vermutlich CPD-Daten braucht."""
    if not text or not text.strip():
        return False
    user_part = text.split("\n\n--- Dokument:", 1)[0]
    user_part = user_part.split("\n\n--- CPD-Wissensbasis ---", 1)[0]
    return _CPD_KEYWORD_RE.search(user_part) is not None
