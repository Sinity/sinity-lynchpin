from __future__ import annotations

import json
from datetime import date, datetime, timezone

from lynchpin.sources import themotte


def test_themotte_iterators_and_daily_activity(tmp_path, monkeypatch) -> None:
    root = tmp_path / "themotte"
    profile = root / "Sinity"
    profile.mkdir(parents=True)
    (profile / themotte.MESSAGE_FILENAME).write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "1",
                        "created_at": "2026-02-01T10:00:00Z",
                        "author": "Sinity",
                        "recipient": "self_made_human",
                        "peer": "self_made_human",
                        "body": "hello",
                        "url": "https://www.themotte.org/comment/1",
                    }
                ),
                json.dumps(
                    {
                        "id": "2",
                        "created_at": "2026-02-02T10:00:00Z",
                        "author": "self_made_human",
                        "recipient": "Sinity",
                        "peer": "self_made_human",
                        "body": "reply",
                        "url": "https://www.themotte.org/comment/2",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (profile / themotte.NOTIFICATION_FILENAME).write_text(
        json.dumps(
            {
                "id": "n1",
                "created_at": "2026-02-01T11:00:00Z",
                "kind": "Username Mention",
                "actor": "naraburns",
                "title": "Username Mention",
                "text": "mentioned @Sinity",
                "url": "https://www.themotte.org/comment/3",
                "unread": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = type("Cfg", (), {"themotte_root": root, "themotte_username": "Sinity"})()
    monkeypatch.setattr(themotte, "get_config", lambda: cfg)

    rows = list(themotte.iter_messages(start=date(2026, 2, 1), end=date(2026, 2, 2)))
    assert [row.id for row in rows] == ["1"]
    assert rows[0].created_at == datetime(2026, 2, 1, 10, tzinfo=timezone.utc)

    activity = themotte.daily_activity(start=date(2026, 2, 1), end=date(2026, 2, 3))
    assert [(row.date, row.messages, row.outbound_messages, row.notifications, row.peers) for row in activity] == [
        (date(2026, 2, 1), 1, 1, 1, ("self_made_human",)),
        (date(2026, 2, 2), 1, 0, 0, ("self_made_human",)),
    ]

    assert themotte.date_range(root=root, username="Sinity") == (
        datetime(2026, 2, 1, 10, tzinfo=timezone.utc),
        datetime(2026, 2, 2, 10, tzinfo=timezone.utc),
    )
