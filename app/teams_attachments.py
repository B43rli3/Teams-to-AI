"""Hilfsfunktionen für Teams-Dateianhänge (SharePoint/OneDrive)."""

from __future__ import annotations

import re
import uuid
from typing import Any

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def extract_guid_from_etag(etag: str) -> str | None:
    """Extrahiert die GUID aus einem SharePoint/Graph eTag."""
    if not etag:
        return None
    match = _GUID_RE.search(etag)
    return match.group(0) if match else None


def build_reference_attachment(
    drive_item: dict[str, Any],
    *,
    content_url: str | None = None,
) -> dict[str, str]:
    """Erzeugt ein Teams-Reference-Attachment aus einem driveItem."""
    attachment_id = extract_guid_from_etag(str(drive_item.get("eTag", ""))) or str(uuid.uuid4())
    # Teams erwartet webDavUrl (Graph-Doku Example 4) oder webUrl / Share-Link.
    resolved_url = content_url or str(
        drive_item.get("webDavUrl")
        or drive_item.get("webUrl")
        or ""
    )
    if not resolved_url:
        raise ValueError("driveItem enthält keine verwertbare SharePoint-URL.")

    return {
        "id": attachment_id,
        "contentType": "reference",
        "contentUrl": resolved_url,
        "name": str(drive_item.get("name") or "anhang.pdf"),
    }


def append_attachment_markup(html_content: str, attachment_id: str) -> str:
    """Fügt das erforderliche attachment-Tag für Teams-HTML hinzu."""
    markup = f'<attachment id="{attachment_id}"></attachment>'
    if markup in html_content:
        return html_content
    return f"{html_content}<p>{markup}</p>"
