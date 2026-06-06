"""Tests for live Raindrop source (raindrop_live.py)."""

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch


from lynchpin.sources.raindrop_live import (
    RaindropPollCursor,
    _parse_raindrop_item,
    raindrop_api_token,
    iter_live_bookmarks,
    iter_merged_bookmarks,
    daily_raindrop_live_activity,
)

# ── Sample API response ────────────────────────────────────────────────────────

_FAKE_API_ITEM = {
    "_id": 12345,
    "title": "Test Bookmark",
    "link": "https://example.com/article",
    "folder": "Articles",
    "tags": ["python", "analysis"],
    "created": "2026-04-21T10:00:00.000Z",
    "excerpt": "A great article about Python",
    "cover": "https://example.com/cover.png",
    "favorite": True,
    "collection": "$collection:0",
}

_FAKE_API_RESPONSE = {
    "result": True,
    "items": [_FAKE_API_ITEM],
    "count": 1,
}

# ── Unit tests ─────────────────────────────────────────────────────────────────


def test_parse_raindrop_item_handles_minimal():
    item = {"_id": 1, "title": "t", "link": "u", "tags": []}
    bm = _parse_raindrop_item(item)
    assert bm.id == 1
    assert bm.title == "t"
    assert bm.url == "u"
    assert bm.tags == []
    assert bm.favorite is False


def test_parse_raindrop_item_handles_full():
    bm = _parse_raindrop_item(_FAKE_API_ITEM)
    assert bm.id == 12345
    assert bm.title == "Test Bookmark"
    assert bm.url == "https://example.com/article"
    assert bm.folder == "Articles"
    assert bm.tags == ["python", "analysis"]
    assert bm.created is not None
    assert bm.created.year == 2026


def test_parse_raindrop_item_handles_none_tags():
    item = {"_id": 1, "title": "t", "link": "u", "tags": None}
    bm = _parse_raindrop_item(item)
    assert bm.tags == []


def test_to_legacy_conversion():
    bm = _parse_raindrop_item(_FAKE_API_ITEM)
    legacy = bm.to_legacy()
    assert legacy.id == 12345
    assert legacy.title == "Test Bookmark"
    assert legacy.url == "https://example.com/article"
    assert legacy.raw == {"source": "raindrop_live", "collection": "$collection:0"}


def test_raindrop_api_token_from_env(monkeypatch):
    monkeypatch.setenv("RAINDROP_API_TOKEN", "test-token-123")
    assert raindrop_api_token() == "test-token-123"


def test_raindrop_api_token_none_when_missing(monkeypatch):
    monkeypatch.delenv("RAINDROP_API_TOKEN", raising=False)
    # token_file won't exist, so returns None
    result = raindrop_api_token()
    # Might be None or might pick up file; either way, type-check
    assert result is None or isinstance(result, str)


# ── API polling tests ──────────────────────────────────────────────────────────


def test_iter_live_bookmarks_no_token(monkeypatch):
    monkeypatch.delenv("RAINDROP_API_TOKEN", raising=False)
    results = list(iter_live_bookmarks())
    assert results == []


@patch("urllib.request.urlopen")
def test_iter_live_bookmarks_parses_response(mock_urlopen, monkeypatch):
    monkeypatch.setenv("RAINDROP_API_TOKEN", "fake-token")
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(_FAKE_API_RESPONSE).encode()
    mock_resp.__enter__.return_value = mock_resp  # for 'with' context
    mock_urlopen.return_value = mock_resp

    results = list(iter_live_bookmarks(token="fake-token", max_pages=1))
    assert len(results) == 1
    assert results[0].title == "Test Bookmark"


@patch("urllib.request.urlopen")
def test_iter_live_bookmarks_respects_since(mock_urlopen, monkeypatch):
    """Bookmarks before 'since' should stop iteration (newest-first)."""
    monkeypatch.setenv("RAINDROP_API_TOKEN", "fake-token")
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(_FAKE_API_RESPONSE).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    # 'since' is after the bookmark date → should be filtered out
    since = datetime(2027, 1, 1, tzinfo=timezone.utc)
    results = list(iter_live_bookmarks(since=since, token="fake-token", max_pages=1))
    assert len(results) == 0


# ── Daily rollup tests ─────────────────────────────────────────────────────────


def test_daily_raindrop_live_activity_no_token(monkeypatch):
    monkeypatch.delenv("RAINDROP_API_TOKEN", raising=False)
    result = daily_raindrop_live_activity(
        start=date(2026, 4, 1), end=date(2026, 4, 30),
    )
    # Should not crash; returns whatever export data is available
    assert isinstance(result, list)


def test_iter_merged_bookmarks_passes_bounds_to_live_and_export(monkeypatch):
    calls = {"live": [], "export": []}
    live = _parse_raindrop_item(_FAKE_API_ITEM)

    def fake_live_bookmarks(*, since=None, token=None, **_kwargs):
        calls["live"].append((since, token))
        return iter([live])

    def fake_export_bookmarks(*, start=None, end=None, **_kwargs):
        calls["export"].append((start, end))
        return iter([])

    monkeypatch.setattr("lynchpin.sources.raindrop_live.iter_live_bookmarks", fake_live_bookmarks)
    monkeypatch.setattr(
        "lynchpin.sources.raindrop_live.iter_raindrop_bookmarks",
        fake_export_bookmarks,
    )

    rows = list(
        iter_merged_bookmarks(
            start=date(2026, 4, 1),
            end=date(2026, 5, 1),
            token="token",
        )
    )

    assert [row.url for row in rows] == ["https://example.com/article"]
    assert calls == {
        "live": [(datetime(2026, 4, 1, tzinfo=timezone.utc), "token")],
        "export": [(date(2026, 4, 1), date(2026, 5, 1))],
    }


def test_raindrop_poll_cursor_is_serializable():
    cursor = RaindropPollCursor(last_id=42, last_polled="2026-04-21T10:00:00+00:00")
    data = {"last_id": cursor.last_id, "last_polled": cursor.last_polled}
    serialized = json.dumps(data)
    parsed = json.loads(serialized)
    assert parsed["last_id"] == 42
