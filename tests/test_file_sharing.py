"""Tests für Datei-Freigabe-Hilfsfunktionen."""

from __future__ import annotations

from app.file_sharing import (
    build_invite_recipient_payloads,
    count_cross_tenant_recipients,
    parse_chat_member_recipients,
)


def test_parse_chat_member_recipients_extracts_email_and_tenant() -> None:
    recipients = parse_chat_member_recipients(
        [
            {
                "userId": "bot-id",
                "email": "bot@stratest.de",
                "tenantId": "tenant-a",
                "displayName": "Bot",
            },
            {
                "userId": "user-id",
                "email": "user@stranext.de",
                "tenantId": "tenant-b",
                "displayName": "User",
            },
        ],
        exclude_user_id="bot-id",
    )
    assert len(recipients) == 1
    assert recipients[0].email == "user@stranext.de"
    assert recipients[0].tenant_id == "tenant-b"


def test_build_invite_recipient_payloads_prefers_email() -> None:
    recipients = parse_chat_member_recipients(
        [
            {
                "userId": "user-id",
                "email": "user@stranext.de",
                "tenantId": "tenant-b",
            }
        ]
    )
    payloads = build_invite_recipient_payloads(recipients)
    assert payloads == [{"email": "user@stranext.de"}]


def test_count_cross_tenant_recipients() -> None:
    recipients = parse_chat_member_recipients(
        [
            {"userId": "u1", "tenantId": "tenant-a"},
            {"userId": "u2", "tenantId": "tenant-b"},
        ]
    )
    assert count_cross_tenant_recipients(recipients, "tenant-a") == 1
