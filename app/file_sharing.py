"""Hilfsfunktionen für OneDrive-/SharePoint-Freigaben (inkl. Cross-Tenant)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ShareRecipient:
    """Empfänger für driveItem/invite."""

    user_id: str = ""
    email: str = ""
    tenant_id: str = ""
    display_name: str = ""


def parse_chat_member_recipients(
    members: list[dict[str, Any]],
    *,
    exclude_user_id: str = "",
) -> list[ShareRecipient]:
    """Extrahiert Freigabe-Empfänger aus Graph-Chat-Mitgliedern."""
    recipients: list[ShareRecipient] = []
    seen_emails: set[str] = set()
    seen_ids: set[str] = set()

    for member in members:
        user_id = str(
            member.get("userId")
            or (member.get("user") or {}).get("id")
            or ""
        ).strip()
        if not user_id or user_id == exclude_user_id:
            continue
        if user_id in seen_ids:
            continue
        seen_ids.add(user_id)

        email = str(member.get("email") or "").strip().lower()
        if email in seen_emails:
            email = ""
        elif email:
            seen_emails.add(email)

        recipients.append(
            ShareRecipient(
                user_id=user_id,
                email=email,
                tenant_id=str(member.get("tenantId") or "").strip(),
                display_name=str(member.get("displayName") or "").strip(),
            )
        )

    return recipients


def count_cross_tenant_recipients(
    recipients: list[ShareRecipient],
    bot_tenant_id: str,
) -> int:
    """Zählt Empfänger außerhalb des Bot-Mandanten."""
    if not bot_tenant_id:
        return 0
    return sum(
        1
        for recipient in recipients
        if recipient.tenant_id and recipient.tenant_id != bot_tenant_id
    )


def build_invite_recipient_payloads(recipients: list[ShareRecipient]) -> list[dict[str, str]]:
    """Baut Graph-Recipient-Objekte (E-Mail bevorzugt für Cross-Tenant)."""
    payloads: list[dict[str, str]] = []
    seen: set[str] = set()

    for recipient in recipients:
        if recipient.email:
            key = f"email:{recipient.email.lower()}"
            if key in seen:
                continue
            seen.add(key)
            payloads.append({"email": recipient.email})
            continue

        if recipient.user_id:
            key = f"id:{recipient.user_id}"
            if key in seen:
                continue
            seen.add(key)
            payloads.append({"objectId": recipient.user_id})

    return payloads
