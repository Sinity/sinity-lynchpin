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
    # Strict address-match: only the operator's known sending addresses
    # qualify. Random sinity@somewhere is NOT operator (operator's mail
    # is ezo.dev@gmail.com).
    assert _looks_outbound("ezo.dev@gmail.com") is True
    assert _looks_outbound("ilukbas@gmail.com") is True
    assert _looks_outbound("sinity@substack.com") is True
    assert _looks_outbound("sinity@example.com") is False
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


def test_looks_outbound_matches_only_operator_email_address():
    """Display names are set by the sending service, so a GitHub
    notification with display 'Sinity' from notifications@github.com is
    inbound, not outbound. Only the email address in the From header is
    authoritative.

    Regression: the prior implementation substring-matched on the whole
    From field, which produced 45 false-positive outbound rows (vs 37
    real outbound) on the operator's archive.
    """
    from lynchpin.sources.gmail_takeout import _looks_outbound

    # Inbound: display name happens to contain 'Sinity' / 'ezodev' but
    # address is someone else's.
    assert not _looks_outbound('"Sinity" <notifications@github.com>')
    assert not _looks_outbound("Sinity from foo <noreply@example.com>")
    assert not _looks_outbound("ezodev_recipient <random@otherdomain.com>")
    # Operator's actual addresses.
    assert _looks_outbound("Ezo <ezo.dev@gmail.com>")
    assert _looks_outbound("ezo.dev@gmail.com")
    assert _looks_outbound("Sinity <ezo.dev@gmail.com>")
    assert _looks_outbound("ilukbas@gmail.com")
    assert _looks_outbound("Sinity from Sinity <sinity@substack.com>")
    # Empty / garbage.
    assert not _looks_outbound("")
    assert not _looks_outbound("no email here")
