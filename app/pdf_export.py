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


def generate_pdf_from_text(
    *, title: str, body: str, images_base64: list[str] | None = None
) -> bytes:
    """Erzeugt ein PDF aus Klartext (UTF-8, inkl. deutscher Umlaute).

    Optional können Bilder (base64, ohne data:-Prefix) eingebettet werden.
    """
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

    if images_base64:
        _try_add_first_image(pdf, images_base64)

    output = pdf.output()
    if isinstance(output, bytearray):
        return bytes(output)
    if isinstance(output, bytes):
        return output
    return str(output).encode("latin-1")


def _try_add_first_image(pdf: object, images_base64: list[str]) -> None:
    """Versucht das erste Bild (base64) als Embedded image hinzuzufügen."""
    import base64
    import os
    import tempfile

    if not images_base64:
        return

    raw = ""
    try:
        raw = images_base64[0]
        img_bytes = base64.b64decode(raw, validate=True)
    except Exception:
        return

    suffix = _detect_image_suffix(img_bytes)
    if not suffix:
        return

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=f".{suffix}", delete=False
        ) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(img_bytes)

        # Neue Seite, damit das Bild nicht mitten in Textauflistungen landet.
        pdf.add_page()
        # fpdf2 nutzt mm als Standard-Einheit. Breite = 190mm auf A4 passt gut.
        pdf.image(tmp_path, x=15, w=180)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _detect_image_suffix(img_bytes: bytes) -> str | None:
    """Erkennt gängige Bildformate über Magic Bytes."""
    if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if img_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if img_bytes.startswith(b"GIF87a") or img_bytes.startswith(b"GIF89a"):
        return "gif"
    if img_bytes.startswith(b"BM"):
        return "bmp"
    # WEBP: "RIFF....WEBP"
    if img_bytes.startswith(b"RIFF") and b"WEBP" in img_bytes[8:16]:
        return "webp"
    return None


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
