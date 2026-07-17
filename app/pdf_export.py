"""PDF-Erzeugung aus LLM-Textantworten."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path

from app.logging_config import get_logger

logger = get_logger(__name__)

_FONT_RESOURCE = "DejaVuSans.ttf"


def default_pdf_filename(*, message_id: str | None = None) -> str:
    """Erzeugt einen sicheren Standarddateinamen für PDF-Antworten."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = ""
    if message_id:
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "", message_id)[:12]
        if safe_id:
            suffix = f"-{safe_id}"
    return f"ai-antwort-{stamp}{suffix}.pdf"


def generate_pdf_from_text(*, title: str, body: str) -> bytes:
    """Erzeugt ein PDF aus Klartext (UTF-8, inkl. deutscher Umlaute)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    font_path = _resolve_font_path()
    font_family = "Helvetica"
    if font_path is not None:
        pdf.add_font("DejaVu", fname=str(font_path))
        font_family = "DejaVu"
    else:
        logger.warning("pdf_font_missing_using_fallback")
        body = _ascii_fallback(body)
        title = _ascii_fallback(title)

    pdf.set_font(font_family, size=14)
    pdf.multi_cell(0, 8, title.strip() or "KI-Antwort")
    pdf.ln(4)
    pdf.set_font(font_family, size=11)

    for paragraph in _split_paragraphs(body):
        pdf.multi_cell(0, 6, paragraph)
        pdf.ln(2)

    output = pdf.output()
    if isinstance(output, bytearray):
        return bytes(output)
    if isinstance(output, bytes):
        return output
    return str(output).encode("latin-1")


def _resolve_font_path() -> Path | None:
    try:
        resource = files("app.assets").joinpath(_FONT_RESOURCE)
        with as_file(resource) as path:
            if path.exists():
                return path
    except (FileNotFoundError, ModuleNotFoundError, TypeError, ValueError):
        pass
    return None


def _split_paragraphs(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return ["(Kein Inhalt)"]
    parts = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    return parts or [cleaned]


def _ascii_fallback(text: str) -> str:
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("latin-1", errors="replace").decode("latin-1")
