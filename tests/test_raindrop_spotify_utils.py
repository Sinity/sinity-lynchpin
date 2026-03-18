"""Tests for pure helpers in sources/exports/raindrop.py and spotify.py."""

from __future__ import annotations

import pytest

from lynchpin.sources.exports.raindrop import _parse_datetime, _parse_tags, _strip
from lynchpin.sources.exports.spotify import _extract_artist, _extract_track, _parse_time


# ---------------------------------------------------------------------------
# raindrop._parse_tags
# ---------------------------------------------------------------------------

class TestParseTags:
    def test_empty_returns_empty(self) -> None:
        assert _parse_tags("") == []

    def test_none_returns_empty(self) -> None:
        assert _parse_tags(None) == []

    def test_comma_separated(self) -> None:
        result = _parse_tags("python, rust, nix")
        assert result == ["python", "rust", "nix"]

    def test_semicolon_separated(self) -> None:
        result = _parse_tags("python;rust;nix")
        assert result == ["python", "rust", "nix"]

    def test_single_tag(self) -> None:
        assert _parse_tags("programming") == ["programming"]

    def test_whitespace_stripped(self) -> None:
        result = _parse_tags("  a  ,  b  ")
        assert result == ["a", "b"]

    def test_empty_items_excluded(self) -> None:
        result = _parse_tags("a,,b")
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# raindrop._parse_datetime
# ---------------------------------------------------------------------------

class TestRaindropParseDatetime:
    def test_iso_z_format(self) -> None:
        result = _parse_datetime("2026-03-17T10:30:00.000Z")
        assert result is not None
        assert result.year == 2026

    def test_slash_date_format(self) -> None:
        result = _parse_datetime("03/17/2026")
        assert result is not None
        assert result.month == 3
        assert result.day == 17

    def test_dash_date_only(self) -> None:
        result = _parse_datetime("2026-03-17")
        assert result is not None
        assert result.year == 2026

    def test_none_returns_none(self) -> None:
        assert _parse_datetime(None) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_datetime("") is None

    def test_invalid_returns_none(self) -> None:
        assert _parse_datetime("not-a-date") is None


# ---------------------------------------------------------------------------
# raindrop._strip
# ---------------------------------------------------------------------------

class TestStrip:
    def test_whitespace_stripped(self) -> None:
        assert _strip("  hello  ") == "hello"

    def test_plain_string_unchanged(self) -> None:
        assert _strip("hello") == "hello"

    def test_none_returns_none(self) -> None:
        assert _strip(None) is None

    def test_whitespace_only_returns_none(self) -> None:
        assert _strip("   ") is None

    def test_empty_returns_none(self) -> None:
        assert _strip("") is None


# ---------------------------------------------------------------------------
# spotify._parse_time
# ---------------------------------------------------------------------------

class TestSpotifyParseTime:
    def test_end_time_format(self) -> None:
        entry = {"endTime": "2026-03-17 14:30"}
        result = _parse_time(entry)
        assert result is not None
        assert result.year == 2026
        assert result.hour == 14

    def test_ts_format_with_z(self) -> None:
        entry = {"ts": "2026-03-17T14:30:00Z"}
        result = _parse_time(entry)
        assert result is not None
        assert result.year == 2026

    def test_empty_entry_returns_none(self) -> None:
        assert _parse_time({}) is None

    def test_end_time_preferred_over_ts(self) -> None:
        entry = {"endTime": "2026-03-17 14:30", "ts": "2025-01-01T00:00:00Z"}
        result = _parse_time(entry)
        assert result is not None
        assert result.year == 2026  # endTime used, not ts


# ---------------------------------------------------------------------------
# spotify._extract_artist
# ---------------------------------------------------------------------------

class TestExtractArtist:
    def test_artist_name_key(self) -> None:
        assert _extract_artist({"artistName": "Boards of Canada"}) == "Boards of Canada"

    def test_master_metadata_key(self) -> None:
        entry = {"master_metadata_album_artist_name": "Aphex Twin"}
        assert _extract_artist(entry) == "Aphex Twin"

    def test_episode_show_name_key(self) -> None:
        entry = {"episode_show_name": "Lex Fridman Podcast"}
        assert _extract_artist(entry) == "Lex Fridman Podcast"

    def test_missing_returns_empty(self) -> None:
        assert _extract_artist({}) == ""

    def test_first_key_takes_priority(self) -> None:
        entry = {"artistName": "First", "master_metadata_album_artist_name": "Second"}
        assert _extract_artist(entry) == "First"


# ---------------------------------------------------------------------------
# spotify._extract_track
# ---------------------------------------------------------------------------

class TestExtractTrack:
    def test_track_name_key(self) -> None:
        assert _extract_track({"trackName": "Roygbiv"}) == "Roygbiv"

    def test_master_metadata_key(self) -> None:
        assert _extract_track({"master_metadata_track_name": "Windowlicker"}) == "Windowlicker"

    def test_episode_name_key(self) -> None:
        assert _extract_track({"episode_name": "Episode 123"}) == "Episode 123"

    def test_audiobook_title_key(self) -> None:
        assert _extract_track({"audiobook_title": "SICP"}) == "SICP"

    def test_missing_returns_empty(self) -> None:
        assert _extract_track({}) == ""
