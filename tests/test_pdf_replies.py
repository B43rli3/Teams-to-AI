"""Tests für PDF-Export und Intent-Erkennung."""

from __future__ import annotations

from app.language_guard import looks_predominantly_english
from app.llm_prompts import build_system_prompt
from app.pdf_export import generate_pdf_from_text
from app.reply_intent import wants_pdf_attachment
from app.teams_attachments import (
    append_attachment_markup,
    build_reference_attachment,
    extract_guid_from_etag,
)


def test_wants_pdf_attachment_detects_german_request() -> None:
    assert wants_pdf_attachment("Bitte als PDF senden")
    assert wants_pdf_attachment("/ai Erstelle mir ein PDF mit einer Zusammenfassung")
    assert wants_pdf_attachment("Schick mir das als PDF")
    assert not wants_pdf_attachment("Was steht in der Datei?")
    assert not wants_pdf_attachment("Was ist das für ein Dokument?")
    assert not wants_pdf_attachment("Fasse mir folgende PDF zusammen?")
    assert not wants_pdf_attachment(
        "Was steht in der PDF?\n\n--- Dokument: Test.pdf ---\nInhalt"
    )


def test_build_system_prompt_includes_german_rule() -> None:
    prompt = build_system_prompt("Basis", include_pdf_hint=True)
    assert "Deutsch" in prompt
    assert "PDF" in prompt


def test_generate_pdf_from_text_returns_pdf_bytes() -> None:
    data = generate_pdf_from_text(
        title="Test",
        body="Hallo Welt\n\nZweiter Absatz mit Umlauten: äöü ÄÖÜ ß",
    )
    assert data.startswith(b"%PDF")


def test_looks_predominantly_english() -> None:
    assert looks_predominantly_english(
        "This is a detailed answer and you can use this information for your work."
    )
    assert not looks_predominantly_english(
        "Das ist eine ausführliche Antwort auf Deutsch mit mehreren deutschen Wörtern."
    )


def test_build_reference_attachment_prefers_webdav_url() -> None:
    attachment = build_reference_attachment(
        {
            "eTag": '"668f7fa8-8129-4de7-b32b-fe1b442e6ef1",1"',
            "webUrl": "https://contoso.sharepoint.com/sites/x/file.pdf",
            "webDavUrl": "https://contoso.sharepoint.com/dav/file.pdf",
            "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/dl/file.pdf?download=1",
            "name": "file.pdf",
        }
    )
    assert attachment["contentUrl"] == "https://contoso.sharepoint.com/dav/file.pdf"


def test_build_reference_attachment_uses_explicit_content_url() -> None:
    attachment = build_reference_attachment(
        {
            "eTag": '"668f7fa8-8129-4de7-b32b-fe1b442e6ef1",1"',
            "webUrl": "https://contoso.sharepoint.com/sites/x/file.pdf",
            "name": "file.pdf",
        },
        content_url="https://contoso.sharepoint.com/:b:/g/personal/user/file.pdf",
    )
    assert attachment["contentUrl"].startswith("https://contoso.sharepoint.com/:b:/g/")


def test_build_reference_attachment_from_drive_item() -> None:
    guid = "668f7fa8-8129-4de7-b32b-fe1b442e6ef1"
    attachment = build_reference_attachment(
        {
            "eTag": f'"{guid},2"',
            "webDavUrl": "https://contoso.sharepoint.com/file.pdf",
            "name": "antwort.pdf",
        }
    )
    assert attachment["id"] == guid
    assert attachment["contentType"] == "reference"
    assert attachment["name"] == "antwort.pdf"


def test_extract_guid_from_etag() -> None:
    assert extract_guid_from_etag('"668f7fa8-8129-4de7-b32b-fe1b442e6ef1",2"') == (
        "668f7fa8-8129-4de7-b32b-fe1b442e6ef1"
    )


def test_append_attachment_markup() -> None:
    html = "<p>Hallo</p>"
    result = append_attachment_markup(html, "abc-123")
    assert 'attachment id="abc-123"' in result
