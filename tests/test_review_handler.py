"""Tests for review_handler.py - JSON parse failure must not treat comments as resolved."""

import json
from unittest.mock import MagicMock, patch

import pytest

from review_handler import extract_json, handle_line_comment, validate_comment


def _make_client(response_text: str) -> MagicMock:
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client.messages.create.return_value = msg
    return client


def _make_event(
    user_type: str = "User",
    in_reply_to_id: int | None = None,
) -> dict:
    return {
        "comment": {
            "id": 1,
            "body": "Please fix this",
            "path": "src/main.py",
            "diff_hunk": "@@ -1,3 +1,3 @@\n-old\n+new",
            "user": {"type": user_type},
            "in_reply_to_id": in_reply_to_id,
        },
        "pull_request": {
            "number": 42,
            "head": {"sha": "abc123"},
        },
    }


class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"key": "value"}') == {"key": "value"}

    def test_markdown_fenced_json(self):
        text = '```json\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_fenced_without_language(self):
        text = '```\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            extract_json("not json")


class TestValidateComment:
    def test_returns_dict_on_valid_json(self):
        payload = '{"validity":"valid","severity":"major","explanation":"ok","should_fix":true}'
        result = validate_comment(_make_client(payload), "comment", "file.py", "diff")
        assert result == {"validity": "valid", "severity": "major", "explanation": "ok", "should_fix": True}

    def test_returns_none_on_invalid_json(self):
        """JSON未出力時は None を返しコメントをスキップする。"""
        result = validate_comment(_make_client("This is not JSON"), "comment", "file.py", "diff")
        assert result is None

    def test_returns_none_on_empty_content(self):
        """レスポンスが空（IndexError）のときも None を返す。"""
        client = MagicMock()
        msg = MagicMock()
        msg.content = []
        client.messages.create.return_value = msg
        result = validate_comment(client, "comment", "file.py", "diff")
        assert result is None

    def test_returns_none_on_partial_markdown_with_no_json(self):
        result = validate_comment(_make_client("```\nnot json\n```"), "comment", "file.py", "diff")
        assert result is None


class TestHandleLineComment:
    def test_skips_bot_comment(self):
        with patch("review_handler.post_reply") as mock_reply:
            handle_line_comment(_make_event(user_type="Bot"))
        mock_reply.assert_not_called()

    def test_skips_reply_comment(self):
        with patch("review_handler.post_reply") as mock_reply:
            handle_line_comment(_make_event(in_reply_to_id=99))
        mock_reply.assert_not_called()

    def test_no_reply_posted_when_validation_json_missing(self):
        """JSON未出力時に指摘を対応済み扱いしないこと（=返信を投稿しないこと）。"""
        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}),
            patch("review_handler.get_file_content", return_value=None),
            patch("review_handler.validate_comment", return_value=None),
            patch("review_handler.post_reply") as mock_reply,
        ):
            handle_line_comment(_make_event())

        mock_reply.assert_not_called()

    def test_reply_posted_when_validation_succeeds(self):
        validation = {"validity": "valid", "severity": "major", "explanation": "ok", "should_fix": False}
        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}),
            patch("review_handler.get_file_content", return_value=None),
            patch("review_handler.validate_comment", return_value=validation),
            patch("review_handler.post_reply") as mock_reply,
        ):
            handle_line_comment(_make_event())

        mock_reply.assert_called_once()

    def test_no_fix_generated_when_should_fix_false(self):
        validation = {"validity": "valid", "severity": "minor", "explanation": "ok", "should_fix": False}
        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}),
            patch("review_handler.get_file_content", return_value=None),
            patch("review_handler.validate_comment", return_value=validation),
            patch("review_handler.generate_fix") as mock_fix,
            patch("review_handler.post_reply"),
        ):
            handle_line_comment(_make_event())

        mock_fix.assert_not_called()

    def test_fix_generated_when_should_fix_true(self):
        validation = {"validity": "valid", "severity": "critical", "explanation": "ok", "should_fix": True}
        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}),
            patch("review_handler.get_file_content", return_value=None),
            patch("review_handler.validate_comment", return_value=validation),
            patch("review_handler.generate_fix", return_value="fix code") as mock_fix,
            patch("review_handler.post_reply"),
        ):
            handle_line_comment(_make_event())

        mock_fix.assert_called_once()
