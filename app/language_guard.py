"""Erkennung offensichtlich englischer LLM-Antworten."""

from __future__ import annotations

_ENGLISH_MARKERS = (
    " the ",
    " is ",
    " are ",
    " and ",
    " you ",
    " your ",
    " this ",
    " that ",
    " with ",
    " for ",
    " can ",
    " have ",
)

_GERMAN_MARKERS = (
    " der ",
    " die ",
    " das ",
    " und ",
    " ist ",
    " nicht ",
    " sie ",
    " ein ",
    " eine ",
    " mit ",
    " für ",
)


def looks_predominantly_english(text: str) -> bool:
    """Heuristik: True, wenn die Antwort eher Englisch als Deutsch wirkt."""
    if not text or len(text.strip()) < 40:
        return False

    lower = f" {text.lower()} "
    english_hits = sum(1 for marker in _ENGLISH_MARKERS if marker in lower)
    german_hits = sum(1 for marker in _GERMAN_MARKERS if marker in lower)
    return english_hits >= 2 and english_hits > german_hits
