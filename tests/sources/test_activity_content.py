from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from lynchpin.sources import activity_content
from lynchpin.sources.activity_content import iter_activity_content_days, iter_activity_title_usage


def test_activity_content_reader(tmp_path) -> None:
    path = tmp_path / "daily.ndjson"
    path.write_text(
        json.dumps(
            {
                "date": "2026-05-24",
                "focused_seconds": 120.0,
                "matched_seconds": 90.0,
                "gpt_matched_seconds": 60.0,
                "unmatched_seconds": 30.0,
                "matched_ratio": 0.75,
                "gpt_matched_ratio": 0.5,
                "activity_seconds": {"implementation": 90.0},
                "content_type_seconds": {"code": 80.0},
                "attention_seconds": {"deep": 70.0},
                "topic_seconds": {"lynchpin": 60.0},
                "platform_seconds": {"codex": 30.0},
                "source_counts": {"gpt": 1, "rules": 2},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    row = next(iter_activity_content_days(path))

    assert row.date.isoformat() == "2026-05-24"
    assert row.matched_ratio == 0.75
    assert row.activity_seconds == {"implementation": 90.0}
    assert row.source_counts == {"gpt": 1, "rules": 2}


def test_activity_content_reader_filters_half_open_window(tmp_path) -> None:
    path = tmp_path / "daily.ndjson"
    rows = [
        {"date": "2026-05-23", "focused_seconds": 60.0},
        {"date": "2026-05-24", "focused_seconds": 120.0},
        {"date": "2026-05-25", "focused_seconds": 180.0},
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    filtered = list(
        iter_activity_content_days(
            path,
            start=date(2026, 5, 24),
            end=date(2026, 5, 25),
        )
    )

    assert [row.date for row in filtered] == [date(2026, 5, 24)]


def test_activity_title_usage_reader(tmp_path) -> None:
    path = tmp_path / "title_usage.ndjson"
    path.write_text(
        json.dumps(
            {
                "title_hash": "abc",
                "app": "kitty",
                "normalized_title": "lynchpin",
                "example_title": "✳ lynchpin",
                "focused_seconds": 300.0,
                "span_count": 2,
                "first_date": "2026-05-23",
                "last_date": "2026-05-24",
                "matched": True,
                "classification_source": "gpt",
                "confidence": 0.9,
                "activity": "coding",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    row = next(iter_activity_title_usage(path))

    assert row.title_hash == "abc"
    assert row.matched is True
    assert row.first_date and row.first_date.isoformat() == "2026-05-23"
    assert row.activity == "coding"


def test_activity_title_usage_reader_filters_overlapping_window(tmp_path) -> None:
    path = tmp_path / "title_usage.ndjson"
    rows = [
        {
            "title_hash": "before",
            "app": "kitty",
            "normalized_title": "before",
            "first_date": "2026-05-20",
            "last_date": "2026-05-22",
        },
        {
            "title_hash": "inside",
            "app": "kitty",
            "normalized_title": "inside",
            "first_date": "2026-05-23",
            "last_date": "2026-05-24",
        },
        {
            "title_hash": "after",
            "app": "kitty",
            "normalized_title": "after",
            "first_date": "2026-05-25",
            "last_date": "2026-05-26",
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    filtered = list(
        iter_activity_title_usage(
            path,
            start=date(2026, 5, 23),
            end=date(2026, 5, 25),
        )
    )

    assert [row.title_hash for row in filtered] == ["inside"]


def test_activity_content_default_reader_materializes_window(
    tmp_path,
    monkeypatch,
) -> None:
    calls = []
    derived = tmp_path / "derived"
    target = derived / "activity_content/daily.ndjson"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"date": "2026-05-24", "focused_seconds": 120.0}) + "\n",
        encoding="utf-8",
    )

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(activity_content, "get_config", lambda: SimpleNamespace(derived_root=derived))
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)

    rows = list(iter_activity_content_days(start=date(2026, 5, 24), end=date(2026, 5, 25)))

    assert calls == [("activity_content", (date(2026, 5, 24), date(2026, 5, 25)))]
    assert [row.date for row in rows] == [date(2026, 5, 24)]


def test_activity_title_usage_default_reader_materializes_window(
    tmp_path,
    monkeypatch,
) -> None:
    calls = []
    derived = tmp_path / "derived"
    target = derived / "activity_content/title_usage.ndjson"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "title_hash": "abc",
                "app": "kitty",
                "normalized_title": "lynchpin",
                "first_date": "2026-05-24",
                "last_date": "2026-05-24",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(activity_content, "get_config", lambda: SimpleNamespace(derived_root=derived))
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)

    rows = list(iter_activity_title_usage(start=date(2026, 5, 24), end=date(2026, 5, 25)))

    assert calls == [("activity_content", (date(2026, 5, 24), date(2026, 5, 25)))]
    assert [row.title_hash for row in rows] == ["abc"]


def test_activity_content_explicit_path_does_not_materialize(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "daily.ndjson"
    path.write_text(
        json.dumps({"date": "2026-05-24", "focused_seconds": 120.0}) + "\n",
        encoding="utf-8",
    )

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("explicit path reads must not materialize")

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    assert [row.date for row in iter_activity_content_days(path)] == [date(2026, 5, 24)]


def test_activity_content_reader_can_skip_ensure(tmp_path, monkeypatch) -> None:
    derived = tmp_path / "derived"
    daily = derived / "activity_content/daily.ndjson"
    title_usage = derived / "activity_content/title_usage.ndjson"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        json.dumps({"date": "2026-05-24", "focused_seconds": 120.0}) + "\n",
        encoding="utf-8",
    )
    title_usage.write_text(
        json.dumps(
            {
                "title_hash": "abc",
                "app": "kitty",
                "normalized_title": "lynchpin",
                "first_date": "2026-05-24",
                "last_date": "2026-05-24",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("pre-ensured reads must not materialize again")

    monkeypatch.setattr(activity_content, "get_config", lambda: SimpleNamespace(derived_root=derived))
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    assert [
        row.date
        for row in iter_activity_content_days(
            start=date(2026, 5, 24),
            end=date(2026, 5, 25),
            ensure=False,
        )
    ] == [date(2026, 5, 24)]
    assert [
        row.title_hash
        for row in iter_activity_title_usage(
            start=date(2026, 5, 24),
            end=date(2026, 5, 25),
            ensure=False,
        )
    ] == ["abc"]
