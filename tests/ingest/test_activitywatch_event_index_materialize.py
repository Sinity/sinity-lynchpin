from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace


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
    assert manifest["generation"].startswith("generation-")
    assert all(Path(path).exists() for path in manifest["product_paths"].values())


def test_full_index_repair_rejects_unverified_canonical_row_count(monkeypatch, tmp_path):
    from lynchpin.core.errors import MaterializationError
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    canonical = tmp_path / "activitywatch/events.ndjson"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        json.dumps(
            {
                "bucket": "aw-watcher-window_host",
                "start": "2026-03-15T08:00:00+00:00",
                "end": "2026-03-15T08:30:00+00:00",
                "data": {"app": "only-row"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    canonical.with_suffix(".manifest.json").write_text('{"row_count": 2}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: canonical)

    try:
        mod.materialize_activitywatch_event_index(root=tmp_path)
    except MaterializationError as exc:
        assert "row count does not match" in str(exc)
    else:
        raise AssertionError("expected full-index verification failure")

    assert not (tmp_path / "activitywatch/events_by_day/manifest.json").exists()


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
    monkeypatch.setattr(
        mod,
        "events_from_activitywatch_dbs",
        lambda *_args, **_kwargs: iter(
            [
                SimpleNamespace(
                    bucket="aw-watcher-window_host",
                    start=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
                    end=datetime(2026, 6, 6, 8, 30, tzinfo=timezone.utc),
                    data={"app": "new-window"},
                )
            ]
        ),
    )

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
    assert json.loads(day_window.read_text(encoding="utf-8"))["data"]["app"] == "old-window"
    assert json.loads(day_after.read_text(encoding="utf-8"))["data"]["app"] == "after"
    window_path = Path(manifest["product_paths"]["2026-06-06"])
    window_rows = [json.loads(line) for line in window_path.read_text(encoding="utf-8").splitlines()]
    assert [row["data"]["app"] for row in window_rows] == ["new-window"]
    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["row_counts"] == {"2026-06-05": 1, "2026-06-06": 1, "2026-06-07": 1}
    assert manifest["window_start"] == "2026-06-06"
    assert manifest["window_end"] == "2026-06-07"

    from lynchpin.sources.activitywatch_event_index import iter_indexed_activitywatch_events

    indexed = list(
        iter_indexed_activitywatch_events(
            bucket_prefix="aw-watcher-window_",
            start=datetime(2026, 6, 6, 7, tzinfo=timezone.utc),
            end=datetime(2026, 6, 6, 9, tzinfo=timezone.utc),
            root=tmp_path,
        )
    )
    assert [event.data["app"] for event in indexed] == ["new-window"]


def test_materialize_activitywatch_event_index_reads_only_bounded_raw_tail(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    canonical = tmp_path / "activitywatch/events.ndjson"
    monkeypatch.setattr(mod, "canonical_activitywatch_events_path", lambda: canonical)
    calls: list[tuple[object, object]] = []

    def raw_events(*_args, **kwargs):
        calls.append((kwargs["start"], kwargs["end"]))
        return iter(
            [
                SimpleNamespace(
                    bucket="aw-watcher-window_host",
                    start=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
                    end=datetime(2026, 6, 6, 8, 30, tzinfo=timezone.utc),
                    data={"app": "new-window"},
                )
            ]
        )

    monkeypatch.setattr(mod, "events_from_activitywatch_dbs", raw_events)

    manifest = mod.materialize_activitywatch_event_index(
        root=tmp_path,
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
    )

    assert len(calls) == 1
    assert calls[0][0].replace(tzinfo=None) == datetime(2026, 6, 6, 6)
    assert calls[0][1].replace(tzinfo=None) == datetime(2026, 6, 7, 6)
    assert manifest["row_count"] == 1
    assert manifest["covered_dates"] == ["2026-06-06"]


def test_failed_index_generation_does_not_replace_serving_manifest(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    serving = tmp_path / "activitywatch/events_by_day/generations/serving/2026-06-06.ndjson"
    serving.parent.mkdir(parents=True)
    serving.write_text('{"data":{"app":"serving"}}\n', encoding="utf-8")
    manifest_path = tmp_path / "activitywatch/events_by_day/manifest.json"
    previous = {
        "schema_version": 2,
        "product_paths": {"2026-06-06": str(serving)},
        "row_counts": {"2026-06-06": 1},
        "covered_dates": ["2026-06-06"],
    }
    manifest_path.write_text(json.dumps(previous) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        mod,
        "events_from_activitywatch_dbs",
        lambda *_args, **_kwargs: iter(
            [
                SimpleNamespace(
                    bucket="aw-watcher-window_host",
                    start=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
                    end=datetime(2026, 6, 6, 8, 30, tzinfo=timezone.utc),
                    data={"app": "candidate"},
                )
            ]
        ),
    )
    monkeypatch.setattr(mod, "write_manifest", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected publish failure")))

    try:
        mod.materialize_activitywatch_event_index(
            root=tmp_path,
            start=date(2026, 6, 6),
            end=date(2026, 6, 7),
        )
    except OSError as exc:
        assert str(exc) == "injected publish failure"
    else:
        raise AssertionError("expected manifest publication failure")

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == previous
    assert json.loads(serving.read_text(encoding="utf-8"))["data"]["app"] == "serving"


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
