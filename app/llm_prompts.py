"""Gemeinsame LLM-System-Prompt-Bausteine."""

from __future__ import annotations

GERMAN_LANGUAGE_RULE = (
    "SPRACHE (verbindlich): Antworte ausschließlich auf Deutsch (de-DE). "
    "Verwende keine englischen Sätze oder Formulierungen, auch nicht gemischt. "
    "Englische Fachbegriffe sind nur erlaubt, wenn sie im Deutschen üblich sind "
    "(z. B. PDF, API, Teams)."
)

PDF_REPLY_RULE = (
    "PDF-AUSGABE: Wenn der Benutzer eine PDF-Datei wünscht, liefere den gewünschten "
    "Inhalt vollständig und strukturiert (Überschriften, Absätze, Aufzählungen). "
    "Die PDF-Datei wird automatisch aus deiner Antwort erzeugt und in Teams angehängt."
)

GERMAN_RETRY_PROMPT = (
    "Deine letzte Antwort war nicht auf Deutsch. "
    "Formuliere die komplette Antwort jetzt ausschließlich auf Deutsch."
)

CPD_CONTEXT_RULE = (
    "CPD-WISSENSBASIS: Im Benutzertext kann ein Block 'CPD-Wissensbasis' enthalten sein. "
    "Nutze diese Daten als primäre Quelle für Fragen zu Modellen, Plänen, Projekten "
    "und Bauteilen. Erfinde keine Plan- oder Modellinformationen, die nicht in den "
    "CPD-Daten stehen. Verweise bei Bedarf auf fehlende Angaben."
)


def build_system_prompt(
    base_prompt: str,
    *,
    include_image_hint: bool = False,
    include_pdf_hint: bool = False,
    include_cpd_hint: bool = False,
) -> str:
    """Baut den finalen System-Prompt mit verbindlichen Sprachregeln."""
    parts = [base_prompt.strip(), GERMAN_LANGUAGE_RULE]
    if include_cpd_hint:
        parts.append(CPD_CONTEXT_RULE)
    if include_image_hint:
        parts.append(
            "Der Benutzer kann Bilder anhängen. Beschreibe und nutze sichtbare "
            "Bildinhalte in deiner Antwort. Erfinde keine Details, die nicht "
            "im Bild erkennbar sind."
        )
    if include_pdf_hint:
        parts.append(PDF_REPLY_RULE)
    return "\n\n".join(part for part in parts if part)
