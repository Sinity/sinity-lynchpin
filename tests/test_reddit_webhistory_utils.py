"""Tests for pure helper functions in exports/reddit.py and captures/webhistory.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lynchpin.sources.captures.webhistory import _tokenize_topic
from lynchpin.sources.exports.reddit import _parse_datetime as _reddit_parse_dt
from lynchpin.sources.exports.reddit import _safe_int as _reddit_safe_int


# ---------------------------------------------------------------------------
# _tokenize_topic (webhistory.py)
# Composes _tokenize → strips stopwords, < 3-char tokens, and pure digits
# ---------------------------------------------------------------------------

class TestTokenizeTopic:
    def test_normal_words_kept(self) -> None:
        result = _tokenize_topic("rust python data")
        assert "rust" in result
        assert "python" in result
        assert "data" in result

    def test_stopwords_removed(self) -> None:
        # "the", "and", "of" etc. are stopwords
        result = _tokenize_topic("the rust and the python")
        assert "the" not in result
        assert "and" not in result
        assert "rust" in result

    def test_short_tokens_removed(self) -> None:
        # Tokens < 3 chars excluded
        result = _tokenize_topic("a to in rust")
        assert "a" not in result
        assert "to" not in result
        assert "in" not in result
        assert "rust" in result

    def test_digit_only_tokens_removed(self) -> None:
        result = _tokenize_topic("123 456 rust 789")
        assert "123" not in result
        assert "rust" in result

    def test_empty_returns_empty(self) -> None:
        assert _tokenize_topic("") == []

    def test_all_stopwords_returns_empty(self) -> None:
        # All common stopwords
        result = _tokenize_topic("the of and")
        assert result == []

    def test_lowercased(self) -> None:
        result = _tokenize_topic("RUST PYTHON")
        assert "rust" in result
        assert "python" in result

    def test_mixed_valid_and_invalid(self) -> None:
        # "ai" is 2 chars → removed; "nix" is 3 chars → kept
        result = _tokenize_topic("ai nix rust")
        assert "nix" in result
        assert "rust" in result


# ---------------------------------------------------------------------------
# _parse_datetime (reddit.py)
# Handles " UTC" suffix and standard ISO formats
# ---------------------------------------------------------------------------

class TestRedditParseDt:
    def test_utc_suffix_format(self) -> None:
        result = _reddit_parse_dt("2024-01-15 10:30:00 UTC")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1

    def test_iso_format_with_offset(self) -> None:
        result = _reddit_parse_dt("2024-01-15T10:30:00+00:00")
        assert result is not None
        assert result.year == 2024

    def test_none_returns_none(self) -> None:
        assert _reddit_parse_dt(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _reddit_parse_dt("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _reddit_parse_dt("   ") is None

    def test_invalid_returns_none(self) -> None:
        assert _reddit_parse_dt("not-a-date") is None

    def test_utc_format_has_utc_timezone(self) -> None:
        result = _reddit_parse_dt("2024-01-15 10:30:00 UTC")
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# _safe_int (reddit.py)
# ---------------------------------------------------------------------------

class TestRedditSafeInt:
    def test_valid_string_integer(self) -> None:
        assert _reddit_safe_int("42") == 42

    def test_none_returns_none(self) -> None:
        assert _reddit_safe_int(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _reddit_safe_int("") is None

    def test_invalid_string_returns_none(self) -> None:
        assert _reddit_safe_int("not-a-number") is None

    def test_negative_value_parsed(self) -> None:
        assert _reddit_safe_int("-1") == -1
