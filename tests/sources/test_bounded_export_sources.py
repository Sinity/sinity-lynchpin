from __future__ import annotations

import json
from datetime import datetime, timezone

from lynchpin.sources import outlook, sms, wykop


def _sms_raw(*, msg_id: int, timestamp: datetime, msg_type: str = "2") -> dict[str, object]:
    return {
        "_id": msg_id,
        "thread_id": 1,
        "address": "demo",
        "date": int(timestamp.timestamp() * 1000),
        "body": "hello",
        "type": msg_type,
        "read": "1",
        "seen": "1",
    }


def test_sms_iter_messages_filters_before_sorting(monkeypatch) -> None:
    parsed = [
        _sms_raw(msg_id=1, timestamp=datetime(2026, 5, 1, 12, tzinfo=timezone.utc)),
        _sms_raw(msg_id=2, timestamp=datetime(2026, 5, 2, 12, tzinfo=timezone.utc)),
        _sms_raw(msg_id=3, timestamp=datetime(2026, 5, 3, 12, tzinfo=timezone.utc)),
    ]
    monkeypatch.setattr(sms, "_parse_sms_csv", lambda root=None: iter(parsed))

    rows = list(
        sms.iter_messages(
            start=datetime(2026, 5, 2, tzinfo=timezone.utc),
            end=datetime(2026, 5, 2, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert [row.msg_id for row in rows] == [2]


def test_sms_daily_activity_passes_inclusive_date_window(monkeypatch) -> None:
    parsed = [
        _sms_raw(msg_id=1, timestamp=datetime(2026, 5, 1, 23, 59, tzinfo=timezone.utc)),
        _sms_raw(msg_id=2, timestamp=datetime(2026, 5, 2, 12, tzinfo=timezone.utc)),
        _sms_raw(msg_id=3, timestamp=datetime(2026, 5, 3, 0, 0, tzinfo=timezone.utc)),
    ]
    monkeypatch.setattr(sms, "_parse_sms_csv", lambda root=None: iter(parsed))

    rows = sms.daily_activity(start="2026-05-02", end="2026-05-02")

    assert [(row.date, row.sent_count) for row in rows] == [("2026-05-02", 1)]


def test_sms_summaries_pass_inclusive_date_window(monkeypatch, tmp_path) -> None:
    calls = []
    message = sms.SMSMessage(
        msg_id=1,
        thread_id=7,
        address="demo",
        date=datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
        body="hello",
        msg_type="sent",
        read=True,
        seen=True,
    )

    def fake_iter_messages(root=None, *, start=None, end=None):
        calls.append((root, start, end))
        yield message

    monkeypatch.setattr(sms, "iter_messages", fake_iter_messages)

    threads = sms.thread_summaries(root=tmp_path, start="2026-05-02", end="2026-05-02")
    counterparts = sms.counterpart_stats(root=tmp_path, start="2026-05-02", end="2026-05-02")

    expected_bounds = (
        tmp_path,
        datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 2, 23, 59, 59, 999999, tzinfo=timezone.utc),
    )
    assert calls == [expected_bounds, expected_bounds]
    assert [(row.thread_id, row.message_count, row.sent_count) for row in threads] == [(7, 1, 1)]
    assert counterparts == [("demo", 1)]


def test_outlook_daily_activity_passes_bounds_to_iter_emails(monkeypatch) -> None:
    calls = []

    def fake_iter_emails(*, start=None, end=None):
        calls.append((start, end))
        yield outlook.OutlookEmail(
            message_id="1",
            subject="demo",
            sender="sender",
            sender_email="sender@example.com",
            recipients=(),
            recipient_emails=(),
            date=datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
            body_preview="",
            folder="inbox",
            is_sent=False,
        )

    monkeypatch.setattr(outlook, "iter_emails", fake_iter_emails)

    rows = outlook.daily_activity(start="2026-05-02", end="2026-05-02")

    assert calls == [
        (
            datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 2, 23, 59, 59, 999999, tzinfo=timezone.utc),
        )
    ]
    assert [(row.date, row.inbox_count) for row in rows] == [("2026-05-02", 1)]


def test_outlook_correspondent_stats_passes_bounds_to_iter_emails(monkeypatch) -> None:
    calls = []

    def fake_iter_emails(*, start=None, end=None):
        calls.append((start, end))
        yield outlook.OutlookEmail(
            message_id="1",
            subject="demo",
            sender="sender",
            sender_email="sender@example.com",
            recipients=(),
            recipient_emails=(),
            date=datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
            body_preview="",
            folder="inbox",
            is_sent=False,
        )
        yield outlook.OutlookEmail(
            message_id="2",
            subject="demo",
            sender="operator",
            sender_email=outlook.OPERATOR_EMAIL,
            recipients=("Recipient",),
            recipient_emails=("recipient@example.com",),
            date=datetime(2026, 5, 2, 13, tzinfo=timezone.utc),
            body_preview="",
            folder="sent",
            is_sent=True,
        )

    monkeypatch.setattr(outlook, "iter_emails", fake_iter_emails)

    rows = outlook.correspondent_stats(start="2026-05-02", end="2026-05-02")

    assert calls == [
        (
            datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 2, 23, 59, 59, 999999, tzinfo=timezone.utc),
        )
    ]
    assert rows == [("sender@example.com", 1), ("recipient@example.com", 1)]


def test_outlook_iter_emails_filters_csv_fallback_before_sorting(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(outlook, "_ensure_extracted", lambda: tmp_path)
    csv_rows = [
        outlook.OutlookEmail(
            message_id="1",
            subject="old",
            sender="sender",
            sender_email="sender@example.com",
            recipients=(),
            recipient_emails=(),
            date=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
            body_preview="",
            folder="inbox",
            is_sent=False,
        ),
        outlook.OutlookEmail(
            message_id="2",
            subject="kept",
            sender="sender",
            sender_email="sender@example.com",
            recipients=(),
            recipient_emails=(),
            date=datetime(2026, 5, 2, 12, tzinfo=timezone.utc),
            body_preview="",
            folder="sent",
            is_sent=True,
        ),
    ]
    monkeypatch.setattr(outlook, "_iter_csv_emails", lambda: iter(csv_rows))

    rows = list(
        outlook.iter_emails(
            start=datetime(2026, 5, 2, tzinfo=timezone.utc),
            end=datetime(2026, 5, 2, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert [row.message_id for row in rows] == ["2"]


def test_wykop_daily_activity_passes_bounds_to_raw_iterators(monkeypatch) -> None:
    calls = {"comments": [], "actions": []}

    def fake_comments(*, root=None, start=None, end=None):
        calls["comments"].append((root, start, end))
        yield wykop.WykopComment(
            kind="link_comment",
            comment_id=1,
            created_at=datetime(2026, 5, 2, 12),
            content="hello",
            rating=0,
            url="https://wykop.pl/link/1",
            parent_id=1,
            parent_title="demo",
            parent_url="https://example.com",
            parent_tags=(),
        )

    def fake_actions(*, root=None, start=None, end=None):
        calls["actions"].append((root, start, end))
        yield wykop.WykopAction(
            kind="upvote",
            created_at=datetime(2026, 5, 2, 13),
            target_id=1,
            target_title="demo",
            target_url="https://example.com",
        )

    monkeypatch.setattr(wykop, "iter_comments", fake_comments)
    monkeypatch.setattr(wykop, "iter_actions", fake_actions)

    rows = wykop.daily_activity(start="2026-05-02", end="2026-05-02")

    assert calls == {
        "comments": [(None, datetime(2026, 5, 2).date(), datetime(2026, 5, 2).date())],
        "actions": [(None, datetime(2026, 5, 2).date(), datetime(2026, 5, 2).date())],
    }
    assert [(row.date, row.comments, row.upvotes) for row in rows] == [
        ("2026-05-02", 1, 1)
    ]


def test_wykop_iterators_filter_by_date(tmp_path) -> None:
    root = tmp_path / "wykop"
    root.mkdir()
    (root / "wykop_links_commented.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "comment_id": 1,
                        "comment_created_at": "2026-05-01 12:00:00",
                        "comment_content": "old",
                    }
                ),
                json.dumps(
                    {
                        "comment_id": 2,
                        "comment_created_at": "2026-05-02 12:00:00",
                        "comment_content": "kept",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "wykop_actions.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "plus", "created_at": "2026-05-01 12:00:00"}),
                json.dumps({"type": "minus", "created_at": "2026-05-02 12:00:00"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    comments = list(
        wykop.iter_comments(
            root=root,
            start=datetime(2026, 5, 2).date(),
            end=datetime(2026, 5, 2).date(),
        )
    )
    actions = list(
        wykop.iter_actions(
            root=root,
            start=datetime(2026, 5, 2).date(),
            end=datetime(2026, 5, 2).date(),
        )
    )

    assert [comment.comment_id for comment in comments] == [2]
    assert [action.kind for action in actions] == ["minus"]
