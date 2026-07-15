"""Tests für MessageParser."""

from __future__ import annotations

from app.message_parser import MessageParser, check_mention_trigger


class TestMessageParser:
    def setup_method(self) -> None:
        self.parser = MessageParser(max_response_characters=100)

    def test_html_cleaning(self) -> None:
        html = "<p>Hallo <strong>Welt</strong>!</p>"
        result = self.parser.parse_teams_message(html)
        assert result == "Hallo Welt!"

    def test_mentions_removed(self) -> None:
        html = '<at id="0">Max Mustermann</at> bitte helfen'
        result = self.parser.parse_teams_message(html)
        assert result == "@Max Mustermann bitte helfen"

    def test_script_style_removed(self) -> None:
        html = "<p>Text</p><script>alert('xss')</script><style>.x{}</style>"
        result = self.parser.parse_teams_message(html)
        assert result == "Text"
        assert "script" not in (result or "").lower()

    def test_empty_message_ignored(self) -> None:
        assert self.parser.parse_teams_message("") is None
        assert self.parser.parse_teams_message("<p></p>") is None
        assert self.parser.parse_teams_message("   ") is None

    def test_attachment_only_ignored(self) -> None:
        result = self.parser.parse_teams_message("", has_attachments=True)
        assert result is None

    def test_attachment_only_allowed_returns_empty_string(self) -> None:
        result = self.parser.parse_teams_message(
            "", has_attachments=True, allow_attachment_only=True
        )
        assert result == ""

    def test_whitespace_normalization(self) -> None:
        html = "<p>  Viel   Leerzeichen  </p>"
        result = self.parser.parse_teams_message(html)
        assert result == "Viel Leerzeichen"

    def test_line_breaks_preserved(self) -> None:
        html = "<p>Zeile 1</p><p>Zeile 2</p>"
        result = self.parser.parse_teams_message(html)
        assert "Zeile 1" in (result or "")
        assert "Zeile 2" in (result or "")

    def test_prefix_removal(self) -> None:
        text = "/ai Was ist Python?"
        result = self.parser.remove_prefix(text, "/ai")
        assert result == "Was ist Python?"

    def test_html_escape_in_response(self) -> None:
        text = "<script>alert('xss')</script>"
        html = self.parser.format_llm_response_for_teams(text)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_response_truncation(self) -> None:
        parser = MessageParser(max_response_characters=50)
        long_text = "A" * 100
        html = parser.format_llm_response_for_teams(long_text)
        assert "gekürzt" in html
        assert "A" * 50 in html

    def test_paragraph_formatting(self) -> None:
        text = "Absatz 1\n\nAbsatz 2"
        html = self.parser.format_llm_response_for_teams(text)
        assert "<p>" in html
        assert "<br/>" not in html or "Absatz" in html

    def test_mention_trigger_check(self) -> None:
        mentions = [
            {
                "id": "0",
                "mentioned": {
                    "user": {"id": "user-123", "displayName": "Bot"},
                },
            }
        ]
        assert check_mention_trigger(mentions, "user-123") is True
        assert check_mention_trigger(mentions, "other-user") is False
        assert check_mention_trigger(None, "user-123") is False
