from __future__ import annotations

from datetime import date, datetime

from lynchpin.sources import exports_wykop


def test_wykop_iterators_filter_half_open_logical_date_window(monkeypatch) -> None:
    rows = [
        exports_wykop.WykopEntry(
            id=1,
            created_at=datetime(2026, 5, 4, 12),
            url="",
            content="old",
            tags=[],
            votes_up=None,
            votes_down=None,
        ),
        exports_wykop.WykopEntry(
            id=2,
            created_at=datetime(2026, 5, 5, 12),
            url="",
            content="kept",
            tags=[],
            votes_up=None,
            votes_down=None,
        ),
        exports_wykop.WykopEntry(
            id=3,
            created_at=datetime(2026, 5, 6, 12),
            url="",
            content="future",
            tags=[],
            votes_up=None,
            votes_down=None,
        ),
    ]
    monkeypatch.setattr(exports_wykop, "_profile_file", lambda filename, username=None: object())
    monkeypatch.setattr(exports_wykop, "_load_entries", lambda path: rows)

    filtered = list(
        exports_wykop.iter_wykop_entries(start=date(2026, 5, 5), end=date(2026, 5, 6))
    )

    assert [row.id for row in filtered] == [2]


def test_summarize_wykop_activity_passes_month_bounds(monkeypatch) -> None:
    calls = []

    def fake_link_comments(**kwargs):
        calls.append(("links", kwargs))
        return iter(
            [
                exports_wykop.WykopLinkComment(
                    id=1,
                    created_at=datetime(2026, 5, 5, 12),
                    url="",
                    content="hello",
                    rating=None,
                    link_id=None,
                    link_title="",
                    link_url="",
                    tags=["tag"],
                )
            ]
        )

    def empty_entries(**kwargs):
        calls.append(("entries", kwargs))
        return iter(())

    def empty_entry_comments(**kwargs):
        calls.append(("entry_comments", kwargs))
        return iter(())

    monkeypatch.setattr(exports_wykop, "iter_wykop_link_comments", fake_link_comments)
    monkeypatch.setattr(exports_wykop, "iter_wykop_entries", empty_entries)
    monkeypatch.setattr(exports_wykop, "iter_wykop_entry_comments", empty_entry_comments)

    summary = exports_wykop.summarize_wykop_activity("2026-05", "2026-05")

    assert [(name, kwargs["start"], kwargs["end"]) for name, kwargs in calls] == [
        ("links", date(2026, 5, 1), date(2026, 6, 1)),
        ("entries", date(2026, 5, 1), date(2026, 6, 1)),
        ("entry_comments", date(2026, 5, 1), date(2026, 6, 1)),
    ]
    assert summary.link_comment_counts == {"2026-05": 1}
