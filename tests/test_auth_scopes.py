"""Tests für Scope-Normalisierung in der Authentifizierung."""

from __future__ import annotations

from app.auth import normalize_msal_scopes


def test_offline_access_is_removed() -> None:
    scopes = normalize_msal_scopes(
        ["User.Read", "offline_access", "ChannelMessage.Read.All", "ChannelMessage.Send"]
    )
    assert "offline_access" not in scopes
    assert all("offline_access" not in s for s in scopes)


def test_openid_and_profile_are_removed() -> None:
    scopes = normalize_msal_scopes(["User.Read", "openid", "profile"])
    assert scopes == ["https://graph.microsoft.com/User.Read"]


def test_short_scopes_become_graph_uris() -> None:
    scopes = normalize_msal_scopes(["User.Read", "ChannelMessage.Send"])
    assert scopes == [
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/ChannelMessage.Send",
    ]


def test_full_uris_are_preserved() -> None:
    scopes = normalize_msal_scopes(
        ["https://graph.microsoft.com/User.Read", "offline_access"]
    )
    assert scopes == ["https://graph.microsoft.com/User.Read"]


def test_duplicates_are_removed() -> None:
    scopes = normalize_msal_scopes(["User.Read", "User.Read", "offline_access"])
    assert scopes == ["https://graph.microsoft.com/User.Read"]
