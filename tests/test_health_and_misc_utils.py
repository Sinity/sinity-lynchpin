"""Tests for pure helpers in health, fbmessenger, and polylogue exports."""

from __future__ import annotations

from pathlib import Path

import pytest

from lynchpin.sources.exports.fbmessenger import _clean_text
from lynchpin.sources.exports.health import _is_tar, _parse_samsung_dt, _safe_float
from lynchpin.sources.exports.polylogue import _provider_key


# ---------------------------------------------------------------------------
# _safe_float (health.py)
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_valid_float_parsed(self) -> None:
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_integer_string_accepted(self) -> None:
        assert _safe_float("42") == pytest.approx(42.0)

    def test_invalid_returns_none(self) -> None:
        assert _safe_float("not a float") is None

    def test_empty_returns_none(self) -> None:
        # empty string → ValueError in float() → None
        assert _safe_float("") is None

    def test_negative_float(self) -> None:
        assert _safe_float("-1.5") == pytest.approx(-1.5)


# ---------------------------------------------------------------------------
# _is_tar (health.py)
# ---------------------------------------------------------------------------

class TestIsTar:
    def test_tar_extension(self) -> None:
        assert _is_tar(Path("export.tar")) is True

    def test_tgz_extension(self) -> None:
        assert _is_tar(Path("export.tgz")) is True

    def test_tar_gz_suffix(self) -> None:
        assert _is_tar(Path("export.tar.gz")) is True

    def test_zip_is_not_tar(self) -> None:
        assert _is_tar(Path("export.zip")) is False

    def test_json_is_not_tar(self) -> None:
        assert _is_tar(Path("data.json")) is False


# ---------------------------------------------------------------------------
# _parse_samsung_dt (health.py)
# ---------------------------------------------------------------------------

class TestParseSamsungDt:
    def test_with_microseconds(self) -> None:
        result = _parse_samsung_dt("2026-03-17 10:30:00.000")
        assert result is not None
        assert result.year == 2026
        assert result.hour == 10

    def test_without_microseconds(self) -> None:
        result = _parse_samsung_dt("2026-03-17 10:30:00")
        assert result is not None
        assert result.year == 2026

    def test_invalid_returns_none(self) -> None:
        assert _parse_samsung_dt("not a date") is None

    def test_iso_date_only_returns_none(self) -> None:
        # No time component → doesn't match either format
        assert _parse_samsung_dt("2026-03-17") is None


# ---------------------------------------------------------------------------
# _clean_text (fbmessenger.py)
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_none_returns_none(self) -> None:
        assert _clean_text(None) is None

    def test_plain_string_passthrough(self) -> None:
        assert _clean_text("hello world") == "hello world"

    def test_non_string_coerced_to_string(self) -> None:
        result = _clean_text(42)  # type: ignore[arg-type]
        assert result == "42"

    def test_utf8_string_preserved(self) -> None:
        assert _clean_text("café") == "café"

    def test_empty_string_preserved(self) -> None:
        assert _clean_text("") == ""


# ---------------------------------------------------------------------------
# _provider_key (polylogue.py)
# ---------------------------------------------------------------------------

class TestProviderKey:
    def test_none_returns_all_sentinel(self) -> None:
        assert _provider_key(None) == "__all__"

    def test_provider_string_passthrough(self) -> None:
        assert _provider_key("claude") == "claude"

    def test_empty_string_returns_all_sentinel(self) -> None:
        # Empty string is falsy → "__all__"
        assert _provider_key("") == "__all__"
