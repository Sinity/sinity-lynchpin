from __future__ import annotations

import json
from datetime import date, datetime, timezone


def test_materialize_activitywatch_event_index_writes_logical_day_files(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod
    from lynchpin.sources.activitywatch_event_index import ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION

    canonical = tmp_path / "activitywatch/events.ndjson"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "bucket": "aw-watcher-window_host",
                        "start": "2026-03-15T02:00:00+00:00",
                        "end": "2026-03-15T02:05:00+00:00",
                        "data": {"app": "kitty"},
                    }
                ),
                json.dumps(
                    {
                        "bucket": "aw-watcher-afk_host",
                        "start": "2026-03-15T08:00:00+00:00",
                        "end": "2026-03-15T09:00:00+00:00",
                        "data": {"status": "not-afk"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    canonical.with_suffix(".manifest.json").write_text('{"row_count": 2}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: canonical)

    manifest = mod.materialize_activitywatch_event_index(root=tmp_path)

    assert manifest["schema_version"] == ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION
    assert manifest["row_count"] == 2
    assert manifest["covered_dates"] == ["2026-03-14", "2026-03-15"]
    assert (tmp_path / "activitywatch/events_by_day/2026-03-14.ndjson").exists()
    assert (tmp_path / "activitywatch/events_by_day/2026-03-15.ndjson").exists()


def test_materialize_activitywatch_event_index_replaces_only_requested_window(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    canonical = tmp_path / "activitywatch/events.ndjson"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        json.dumps(
            {
                "bucket": "aw-watcher-window_host",
                "start": "2026-06-06T08:00:00+00:00",
                "end": "2026-06-06T08:30:00+00:00",
                "data": {"app": "new-window"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    canonical.with_suffix(".manifest.json").write_text('{"row_count": 1}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: canonical)

    day_before = tmp_path / "activitywatch/events_by_day/2026-06-05.ndjson"
    day_window = tmp_path / "activitywatch/events_by_day/2026-06-06.ndjson"
    day_after = tmp_path / "activitywatch/events_by_day/2026-06-07.ndjson"
    day_before.parent.mkdir(parents=True)
    day_before.write_text('{"data":{"app":"before"}}\n', encoding="utf-8")
    day_window.write_text('{"data":{"app":"old-window"}}\n', encoding="utf-8")
    day_after.write_text('{"data":{"app":"after"}}\n', encoding="utf-8")
    (tmp_path / "activitywatch/events_by_day/manifest.json").write_text(
        json.dumps(
            {
                "product_paths": {
                    "2026-06-05": str(day_before),
                    "2026-06-06": str(day_window),
                    "2026-06-07": str(day_after),
                },
                "row_counts": {
                    "2026-06-05": 1,
                    "2026-06-06": 1,
                    "2026-06-07": 1,
                },
                "covered_dates": ["2026-06-05", "2026-06-06", "2026-06-07"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = mod.materialize_activitywatch_event_index(
        root=tmp_path,
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
    )

    assert json.loads(day_before.read_text(encoding="utf-8"))["data"]["app"] == "before"
    assert json.loads(day_after.read_text(encoding="utf-8"))["data"]["app"] == "after"
    window_rows = [json.loads(line) for line in day_window.read_text(encoding="utf-8").splitlines()]
    assert [row["data"]["app"] for row in window_rows] == ["new-window"]
    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["row_counts"] == {"2026-06-05": 1, "2026-06-06": 1, "2026-06-07": 1}
    assert manifest["window_start"] == "2026-06-06"
    assert manifest["window_end"] == "2026-06-07"


def test_indexed_activitywatch_events_read_only_relevant_day_files(tmp_path):
    from lynchpin.sources.activitywatch_event_index import (
        activitywatch_event_index_path,
        iter_indexed_activitywatch_events,
    )

    row = {
        "bucket": "aw-watcher-window_host",
        "start": "2026-03-15T10:00:00+00:00",
        "end": "2026-03-15T11:00:00+00:00",
        "data": {"app": "kitty"},
    }
    path = activitywatch_event_index_path(date(2026, 3, 15), tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    activitywatch_event_index_path(date(2026, 3, 17), tmp_path).write_text(
        "not json\n",
        encoding="utf-8",
    )

    events = list(
        iter_indexed_activitywatch_events(
            bucket_prefix="aw-watcher-window_",
            start=datetime(2026, 3, 15, 9, tzinfo=timezone.utc),
            end=datetime(2026, 3, 16, 9, tzinfo=timezone.utc),
            root=tmp_path,
        )
    )

    assert [event.data for event in events] == [{"app": "kitty"}]
