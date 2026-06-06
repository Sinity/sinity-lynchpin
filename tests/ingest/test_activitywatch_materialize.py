from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources.activitywatch_models import AWEvent


def test_materialize_activitywatch_events_records_input_high_water(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.ingest.activitywatch_materialize import ACTIVITYWATCH_EVENTS_SCHEMA_VERSION

    db = tmp_path / "aw.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "events.ndjson"
    cfg = SimpleNamespace(activitywatch_db=db, activitywatch_archive_db_dir=tmp_path / "archive")
    event = AWEvent(
        bucket="aw-watcher-window_host",
        start=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
        end=datetime(2026, 1, 1, 10, 5, tzinfo=timezone.utc),
        data={"app": "kitty"},
    )

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(
        activitywatch_materialize,
        "events_from_activitywatch_dbs",
        lambda _prefix: iter([event]),
    )

    manifest = activitywatch_materialize.materialize_activitywatch_events(output=output)

    assert manifest["row_count"] == 1
    assert manifest["schema_version"] == ACTIVITYWATCH_EVENTS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None


def test_materialize_activitywatch_events_reports_logical_date_bounds(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize

    output = tmp_path / "events.ndjson"
    cfg = SimpleNamespace(activitywatch_db=tmp_path / "missing.db", activitywatch_archive_db_dir=tmp_path / "archive")
    event = AWEvent(
        bucket="aw-watcher-window_host",
        start=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 2, 1, 5, tzinfo=timezone.utc),
        data={"app": "kitty"},
    )

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(
        activitywatch_materialize,
        "events_from_activitywatch_dbs",
        lambda _prefix: iter([event]),
    )

    manifest = activitywatch_materialize.materialize_activitywatch_events(output=output)

    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-01"
    assert manifest["first_timestamp_date"] == "2026-01-02"
    assert manifest["last_timestamp_date"] == "2026-01-02"
    assert manifest["date_boundary"] == "logical_06:00_local"


def test_materialize_activitywatch_events_replaces_only_requested_window(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize

    output = tmp_path / "events.ndjson"
    cfg = SimpleNamespace(activitywatch_db=tmp_path / "aw.db", activitywatch_archive_db_dir=tmp_path / "archive")
    output.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "bucket": "aw-watcher-window_host",
                        "start": "2026-06-05T08:00:00+00:00",
                        "end": "2026-06-05T09:00:00+00:00",
                        "data": {"app": "before"},
                    }
                ),
                json.dumps(
                    {
                        "bucket": "aw-watcher-window_host",
                        "start": "2026-06-06T08:00:00+00:00",
                        "end": "2026-06-06T09:00:00+00:00",
                        "data": {"app": "old-window"},
                    }
                ),
                json.dumps(
                    {
                        "bucket": "aw-watcher-window_host",
                        "start": "2026-06-07T08:00:00+00:00",
                        "end": "2026-06-07T09:00:00+00:00",
                        "data": {"app": "after"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    replacement = AWEvent(
        bucket="aw-watcher-window_host",
        start=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
        end=datetime(2026, 6, 6, 8, 30, tzinfo=timezone.utc),
        data={"app": "new-window"},
    )
    calls: list[tuple[object, datetime | None, datetime | None]] = []

    def fake_events(prefix, *, start=None, end=None):
        calls.append((prefix, start, end))
        assert prefix == activitywatch_materialize.BUCKET_PREFIXES
        return iter([replacement])

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", fake_events)

    manifest = activitywatch_materialize.materialize_activitywatch_events(
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    apps = {row["data"]["app"] for row in rows}
    assert apps == {"before", "new-window", "after"}
    assert manifest["window_start"] == "2026-06-06"
    assert manifest["window_end"] == "2026-06-07"
    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["covered_date_count"] == 3
    assert len(calls) == 1
    assert all(call[1] is not None and call[2] is not None for call in calls)


def test_materialize_activitywatch_events_records_zero_row_window_days(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize

    output = tmp_path / "events.ndjson"
    cfg = SimpleNamespace(activitywatch_db=tmp_path / "aw.db", activitywatch_archive_db_dir=tmp_path / "archive")
    output.write_text(
        json.dumps(
            {
                "bucket": "aw-watcher-window_host",
                "start": "2026-06-05T08:00:00+00:00",
                "end": "2026-06-05T09:00:00+00:00",
                "data": {"app": "before"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps({"covered_dates": ["2026-06-05", "2026-06-09"]}),
        encoding="utf-8",
    )

    def fake_events(prefix, *, start=None, end=None):
        return iter(())

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", fake_events)

    manifest = activitywatch_materialize.materialize_activitywatch_events(
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 8),
    )

    assert manifest["covered_dates"] == [
        "2026-06-05",
        "2026-06-06",
        "2026-06-07",
        "2026-06-09",
    ]
