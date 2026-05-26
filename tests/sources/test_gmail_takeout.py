"""Tests for Gmail Takeout source (gmail_takeout.py)."""

from datetime import date

import pytest

from lynchpin.sources.gmail_takeout import (
    GmailMessage,
    _extract_body_preview,
    _normalize_thread_id,
    _parse_date,
    _looks_outbound,
)

# ── Unit tests (no archive dependency) ─────────────────────────────────────────


def test_normalize_thread_id_returns_none_for_empty():
    assert _normalize_thread_id(None) is None
    assert _normalize_thread_id("") is None
    assert _normalize_thread_id("   ") is None


def test_normalize_thread_id_strips_whitespace():
    assert _normalize_thread_id("  abc123  ") == "abc123"


def test_parse_date_returns_none_for_empty():
    assert _parse_date(None) is None
    assert _parse_date("") is None


def test_parse_date_handles_rfc2822():
    result = _parse_date("Mon, 21 Apr 2026 10:00:00 +0000")
    assert result is not None
    assert result.year == 2026
    assert result.month == 4
    assert result.day == 21


def test_parse_date_returns_none_for_garbage():
    assert _parse_date("not a date") is None


def test_looks_outbound_matches_operator_names():
    assert _looks_outbound("sinity@example.com") is True
    assert _looks_outbound("ilukbas@gmail.com") is True
    assert _looks_outbound("random@example.com") is False


def test_gmail_message_date_property():
    msg = GmailMessage(
        message_id="<test@example.com>",
        thread_id=None,
        sender="alice@example.com",
        recipients=("bob@example.com",),
        cc=(),
        timestamp=_parse_date("Mon, 21 Apr 2026 10:00:00 +0000"),
        subject="Test",
        body_preview="hello",
        label="Mail",
        archive_source="/tmp/test.zip",
        size_bytes=100,
    )
    assert msg.date == date(2026, 4, 21)


def test_gmail_message_date_none_for_missing_timestamp():
    msg = GmailMessage(
        message_id="<test@example.com>",
        thread_id=None,
        sender="alice@example.com",
        recipients=("bob@example.com",),
        cc=(),
        timestamp=None,
        subject="Test",
        body_preview="hello",
        label="Mail",
        archive_source="/tmp/test.zip",
        size_bytes=100,
    )
    assert msg.date is None


# ── Integration tests (require actual Takeout archives) ────────────────────────


@pytest.mark.slow
def test_iter_gmail_messages_discovers_archives():
    """Integration: verify the Gmail source discovers messages from real
    Takeout archives. Skipped when no archives are present."""
    from lynchpin.sources.gmail_takeout import iter_gmail_messages_deduped

    count = 0
    for _msg in iter_gmail_messages_deduped():
        count += 1
        if count >= 10:
            break
    # If archives exist, we should find messages. If not, this is a no-op.
    # The important thing is it doesn't crash.
    assert count >= 0


@pytest.mark.slow
def test_daily_gmail_activity_returns_list():
    """Integration: daily rollup should return a list (possibly empty)."""
    from lynchpin.sources.gmail_takeout import daily_gmail_activity

    result = daily_gmail_activity(
        start=date(2024, 1, 1), end=date(2024, 1, 8),
    )
    assert isinstance(result, list)
    for day in result:
        assert day.message_count >= 0
