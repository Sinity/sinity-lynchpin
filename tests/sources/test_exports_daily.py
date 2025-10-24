from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

from lynchpin.sources import exports_messenger, exports_raindrop
from lynchpin.sources.exports import (
    daily_messenger_activity,
    daily_raindrop_activity,
)


def test_daily_messenger_returns_list():
    result = daily_messenger_activity(start=date(2024, 3, 1), end=date(2024, 12, 31))
    assert isinstance(result, list)


def test_daily_raindrop_returns_list():
    result = daily_raindrop_activity(start=date(2020, 1, 1), end=date(2025, 12, 31))
    assert isinstance(result, list)


def test_messenger_day_activity_fields():
    result = daily_messenger_activity(start=date(2024, 3, 1), end=date(2024, 12, 31))
    if result:
        day = result[0]
        assert hasattr(day, "message_count")
        assert hasattr(day, "thread_count")
        assert hasattr(day, "sent_count")
        assert day.message_count > 0


def test_raindrop_day_activity_fields():
    result = daily_raindrop_activity(start=date(2020, 1, 1), end=date(2025, 12, 31))
    if result:
        day = result[0]
        assert hasattr(day, "bookmarks_added")
        assert day.bookmarks_added > 0


def test_messenger_default_reader_materializes(monkeypatch, tmp_path):
    calls = []
    product = tmp_path / "comms/facebook-messenger/processed/canonical/messages.ndjson"
    product.parent.mkdir(parents=True)
    product.write_text(
        json.dumps(
            {
                "thread_name": "Demo",
                "participants": ["Alice", "Bob"],
                "sender": "Alice",
                "timestamp": "2026-05-05T12:00:00+00:00",
                "text": "hello",
                "kind": "generic",
                "is_unsent": False,
                "media_count": 0,
                "reaction_count": 0,
                "source": "fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(exports_messenger, "get_config", lambda: SimpleNamespace(exports_root=tmp_path))
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    rows = list(exports_messenger.iter_fbmessenger_messages())

    assert calls == [("facebook_messenger", None)]
    assert [row.text for row in rows] == ["hello"]


def test_messenger_daily_uses_single_windowed_materialization(monkeypatch):
    monkeypatch.setattr(exports_messenger, "_OPERATOR_FB_NAMES", frozenset({"Test Operator"}))
    calls = []
    message_calls = []
    messages = [
        exports_messenger.MessengerMessage(
            thread_name="Demo",
            participants=["Test Operator", "Alice"],
            sender="Test Operator",
            timestamp=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
            text="hello",
            kind="generic",
            is_unsent=False,
            media_count=0,
            reaction_count=0,
            source="fixture",
        ),
        exports_messenger.MessengerMessage(
            thread_name="Demo",
            participants=["Test Operator", "Alice"],
            sender="Alice",
            timestamp=datetime(2026, 5, 5, 13, tzinfo=timezone.utc),
            text="reply",
            kind="generic",
            is_unsent=False,
            media_count=0,
            reaction_count=0,
            source="fixture",
        ),
    ]

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    def fake_messages(*, paths=None, start=None, end=None, ensure=True):
        message_calls.append((start, end))
        assert paths is None
        assert start == date(2026, 5, 5)
        assert end == date(2026, 5, 6)
        assert ensure is False
        yield from messages

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)
    monkeypatch.setattr(exports_messenger, "iter_fbmessenger_messages", fake_messages)

    rows = exports_messenger.daily_messenger_activity(
        start=date(2026, 5, 5), end=date(2026, 5, 6)
    )

    assert calls == [("facebook_messenger", (date(2026, 5, 5), date(2026, 5, 6)))]
    assert message_calls == [(date(2026, 5, 5), date(2026, 5, 6))]
    assert rows[0].message_count == 2
    assert rows[0].sent_count == 1


def test_messenger_iterator_filters_half_open_logical_date_window(monkeypatch):
    messages = [
        exports_messenger.MessengerMessage(
            thread_name="Demo",
            participants=["Alice", "Bob"],
            sender="Alice",
            timestamp=datetime(2026, 5, 4, 12, tzinfo=timezone.utc),
            text="old",
            kind="generic",
            is_unsent=False,
            media_count=0,
            reaction_count=0,
            source="fixture",
        ),
        exports_messenger.MessengerMessage(
            thread_name="Demo",
            participants=["Alice", "Bob"],
            sender="Alice",
            timestamp=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
            text="kept",
            kind="generic",
            is_unsent=False,
            media_count=0,
            reaction_count=0,
            source="fixture",
        ),
        exports_messenger.MessengerMessage(
            thread_name="Demo",
            participants=["Alice", "Bob"],
            sender="Alice",
            timestamp=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
            text="future",
            kind="generic",
            is_unsent=False,
            media_count=0,
            reaction_count=0,
            source="fixture",
        ),
    ]
    monkeypatch.setattr(exports_messenger, "_load_messages", lambda paths=None: messages)

    rows = list(
        exports_messenger.iter_fbmessenger_messages(
            start=date(2026, 5, 5), end=date(2026, 5, 6), ensure=False
        )
    )

    assert [row.text for row in rows] == ["kept"]


def test_raindrop_default_reader_materializes(monkeypatch, tmp_path):
    calls = []
    product = tmp_path / "raindrop/processed/bookmarks.csv"
    product.parent.mkdir(parents=True)
    product.write_text(
        "\n".join(
            [
                "id,title,url,folder,tags,created,note,excerpt,cover,favorite",
                "1,Example,https://example.com,Inbox,tag,2026-05-05T12:00:00+00:00,,,false,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        exports_raindrop,
        "get_config",
        lambda: SimpleNamespace(exports_root=tmp_path, raindrop_dir=tmp_path, raindrop_csv=None),
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    rows = list(exports_raindrop.iter_raindrop_bookmarks())

    assert calls == [("raindrop", None)]
    assert [row.title for row in rows] == ["Example"]


def test_raindrop_daily_uses_single_windowed_materialization(monkeypatch):
    calls = []
    bookmark = exports_raindrop.RaindropBookmark(
        id=1,
        title="Example",
        url="https://example.com",
        folder="Inbox",
        tags=["tag"],
        created=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
        note="",
        excerpt="",
        cover=None,
        favorite=False,
        raw={},
    )

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    def fake_bookmarks(csv_path=None, *, start=None, end=None, ensure=True):
        assert csv_path is None
        assert start == date(2026, 5, 5)
        assert end == date(2026, 5, 6)
        assert ensure is False
        yield bookmark

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)
    monkeypatch.setattr(exports_raindrop, "iter_raindrop_bookmarks", fake_bookmarks)

    rows = exports_raindrop.daily_raindrop_activity(
        start=date(2026, 5, 5), end=date(2026, 5, 6)
    )

    assert calls == [("raindrop", (date(2026, 5, 5), date(2026, 5, 6)))]
    assert rows[0].bookmarks_added == 1


def test_raindrop_iterator_filters_half_open_logical_date_window(monkeypatch, tmp_path):
    product = tmp_path / "raindrop/processed/bookmarks.csv"
    product.parent.mkdir(parents=True)
    product.write_text(
        "\n".join(
            [
                "id,title,url,folder,tags,created,note,excerpt,cover,favorite",
                "1,Old,https://old.example,Inbox,,2026-05-04T12:00:00+00:00,,,false,",
                "2,Kept,https://kept.example,Inbox,,2026-05-05T12:00:00+00:00,,,false,",
                "3,Future,https://future.example,Inbox,,2026-05-06T12:00:00+00:00,,,false,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        exports_raindrop,
        "get_config",
        lambda: SimpleNamespace(exports_root=tmp_path, raindrop_dir=tmp_path, raindrop_csv=None),
    )

    rows = list(
        exports_raindrop.iter_raindrop_bookmarks(
            start=date(2026, 5, 5), end=date(2026, 5, 6), ensure=False
        )
    )

    assert [row.title for row in rows] == ["Kept"]
