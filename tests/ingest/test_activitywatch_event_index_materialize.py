from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_materialize_activitywatch_event_index_writes_logical_day_files(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod
    from lynchpin.sources.activitywatch_event_index import ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION

    live_db = tmp_path / "activitywatch.sqlite"
    live_db.write_bytes(b"fixture")
    monkeypatch.setattr(mod, "activitywatch_input_files", lambda _cfg: (live_db,))
    monkeypatch.setattr(
        mod,
        "events_from_activitywatch_dbs",
        lambda *_args, **_kwargs: iter(
            [
                SimpleNamespace(
                    bucket="aw-watcher-window_host",
                    start=datetime(2026, 3, 15, 2, tzinfo=timezone.utc),
                    end=datetime(2026, 3, 15, 2, 5, tzinfo=timezone.utc),
                    data={"app": "kitty"},
                ),
                SimpleNamespace(
                    bucket="aw-watcher-afk_host",
                    start=datetime(2026, 3, 15, 8, tzinfo=timezone.utc),
                    end=datetime(2026, 3, 15, 9, tzinfo=timezone.utc),
                    data={"status": "not-afk"},
                ),
            ]
        ),
    )

    manifest = mod.materialize_activitywatch_event_index(root=tmp_path, full=True)

    assert manifest["schema_version"] == ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION
    assert manifest["row_count"] == 2
    assert manifest["covered_dates"] == ["2026-03-14", "2026-03-15"]
    assert manifest["generation"].startswith("generation-")
    assert manifest["canonical_row_count_verified"] is False
    assert manifest["full_source_scan_completed"] is True
    assert all(Path(path).exists() for path in manifest["product_paths"].values())


def test_unbounded_event_index_rebuild_requires_explicit_full(monkeypatch, tmp_path):
    from lynchpin.core.errors import MaterializationError
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    try:
        mod.materialize_activitywatch_event_index(root=tmp_path)
    except MaterializationError as exc:
        assert "requires explicit full=True" in str(exc)
    else:
        raise AssertionError("expected refusal of unbounded rebuild without explicit opt-in")

    assert not (tmp_path / "activitywatch/events_by_day/manifest.json").exists()


def test_module_cli_requires_and_forwards_full(monkeypatch, capsys):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    calls: list[bool] = []
    monkeypatch.setattr(mod, "materialize_activitywatch_event_index", lambda **kwargs: calls.append(kwargs["full"]) or {})
    try:
        mod.main([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("module CLI accepted an unscoped rebuild without --full")
    assert calls == []

    assert mod.main(["--full"]) == 0
    assert calls == [True]
    assert json.loads(capsys.readouterr().out) == {}


def test_production_source_handler_forwards_full_opt_in(monkeypatch):
    from lynchpin.materializers import handlers

    calls: list[dict[str, object]] = []
    monkeypatch.setitem(
        handlers._SOURCE_HANDLERS,
        "activitywatch_event_index",
        lambda **kwargs: calls.append(kwargs) or {},
    )
    context = SimpleNamespace(
        step=SimpleNamespace(product="activitywatch_event_index", effective_window=None),
        runtime={"full": True, "window": None},
    )

    handlers.run_source_handler(context)

    assert calls == [{"full": True}]


def test_full_rebuild_uses_live_source_past_stale_canonical_carrier(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    canonical = tmp_path / "activitywatch/events.ndjson"
    canonical.parent.mkdir(parents=True)
    canonical.write_text(
        json.dumps(
            {
                "bucket": "aw-watcher-window_host",
                "start": "2026-08-24T08:00:00+00:00",
                "end": "2026-08-24T08:30:00+00:00",
                "data": {"app": "stale-carrier-only"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    canonical.with_suffix(".manifest.json").write_text('{"row_count": 1, "last_date": "2026-08-24"}\n', encoding="utf-8")
    monkeypatch.setattr(mod, "activitywatch_input_files", lambda _cfg: (canonical,))
    observed_calls: list[dict[str, object]] = []

    def live_rows(*_args, **kwargs):
        observed_calls.append(kwargs)
        return iter(
            [
                SimpleNamespace(
                    bucket="aw-watcher-window_host",
                    start=datetime(2026, 8, 24, 8, tzinfo=timezone.utc),
                    end=datetime(2026, 8, 24, 8, 30, tzinfo=timezone.utc),
                    data={"app": "source-before-carrier-end"},
                ),
                SimpleNamespace(
                    bucket="aw-watcher-window_host",
                    start=datetime(2026, 9, 26, 8, tzinfo=timezone.utc),
                    end=datetime(2026, 9, 26, 8, 30, tzinfo=timezone.utc),
                    data={"app": "source-after-carrier-end"},
                ),
            ]
        )

    monkeypatch.setattr(mod, "events_from_activitywatch_dbs", live_rows)
    manifest = mod.materialize_activitywatch_event_index(root=tmp_path, full=True)

    assert observed_calls == [
        {"start": None, "end": None, "databases": (canonical,), "order": "bucket", "dedupe": False}
    ]
    assert manifest["covered_dates"] == ["2026-08-24", "2026-09-26"]
    assert manifest["last_date"] == "2026-09-26"
    assert manifest["canonical_row_count_verified"] is False
    assert manifest["full_source_scan_completed"] is True
    indexed_rows = [
        json.loads(line)
        for path in manifest["product_paths"].values()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
    ]
    assert {row["data"]["app"] for row in indexed_rows} == {
        "source-before-carrier-end",
        "source-after-carrier-end",
    }


def test_materialize_activitywatch_event_index_replaces_only_requested_window(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    source_db = tmp_path / "aw.db"
    source_db.write_bytes(b"fixture")
    monkeypatch.setattr(mod, "activitywatch_input_files", lambda _cfg: (source_db,))

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


def test_event_index_repairs_missing_member_only_inside_requested_window(
    monkeypatch, tmp_path
):
    from lynchpin.core.errors import MaterializationError
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    def seed(root: Path, missing_day: date) -> None:
        manifest_path = root / "activitywatch/events_by_day/manifest.json"
        manifest_path.parent.mkdir(parents=True)
        missing = manifest_path.parent / "generations/old" / f"{missing_day}.ndjson"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "product_paths": {missing_day.isoformat(): str(missing)},
                    "row_counts": {missing_day.isoformat(): 1},
                    "covered_dates": [missing_day.isoformat()],
                }
            ),
            encoding="utf-8",
        )

    event = {
        "bucket": "aw-watcher-window_host",
        "start": "2026-06-06T08:00:00+00:00",
        "end": "2026-06-06T08:30:00+00:00",
        "data": {"app": "synthetic"},
    }
    monkeypatch.setattr(mod, "_iter_tail_rows", lambda **_kwargs: iter((event,)))

    inside_root = tmp_path / "inside"
    seed(inside_root, date(2026, 6, 6))
    inside_input = inside_root / "events.ndjson"
    inside_input.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "activitywatch_input_files", lambda _cfg: (inside_input,))
    repaired = mod.materialize_activitywatch_event_index(
        root=inside_root,
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
    )
    repaired_path = Path(repaired["product_paths"]["2026-06-06"])
    repaired_row = json.loads(repaired_path.read_text(encoding="utf-8"))
    assert repaired_row["data"]["app"] == "synthetic"

    outside_root = tmp_path / "outside"
    seed(outside_root, date(2026, 6, 5))
    outside_input = outside_root / "events.ndjson"
    outside_input.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "activitywatch_input_files", lambda _cfg: (outside_input,))
    with pytest.raises(MaterializationError, match="2026-06-05"):
        mod.materialize_activitywatch_event_index(
            root=outside_root,
            start=date(2026, 6, 6),
            end=date(2026, 6, 7),
        )


def test_materialize_activitywatch_event_index_reads_only_bounded_raw_tail(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_event_index_materialize as mod

    source_db = tmp_path / "aw.db"
    source_db.write_bytes(b"fixture")
    monkeypatch.setattr(mod, "activitywatch_input_files", lambda _cfg: (source_db,))

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

    source_db = tmp_path / "aw.db"
    source_db.write_bytes(b"fixture")
    monkeypatch.setattr(mod, "activitywatch_input_files", lambda _cfg: (source_db,))

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


def test_event_index_freshness_tracks_committed_wal_state_it_read(monkeypatch, tmp_path):
    import sqlite3

    from lynchpin import materialization
    from lynchpin.ingest import activitywatch_event_index_materialize as mod
    from lynchpin.sources import activitywatch_raw

    db = tmp_path / "aw.db"
    cfg = SimpleNamespace(activitywatch_db=db, activitywatch_archive_db_dir=tmp_path / "archive")
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_raw, "get_config", lambda: cfg)
    writer = sqlite3.connect(db)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE buckets (id INTEGER PRIMARY KEY, name TEXT)")
    writer.execute("CREATE TABLE events (bucketrow INTEGER, starttime INTEGER, endtime INTEGER, data TEXT)")
    writer.execute("INSERT INTO buckets VALUES (1, 'aw-watcher-window_host')")

    def append(app: str, hour: int) -> None:
        start = int(datetime(2026, 6, 6, hour, tzinfo=timezone.utc).timestamp()) * 10**9
        writer.execute("INSERT INTO events VALUES (1, ?, ?, ?)", (start, start + 60 * 10**9, json.dumps({"app": app})))
        writer.commit()

    try:
        append("first", 10)
        manifest = mod.materialize_activitywatch_event_index(root=tmp_path, full=True)
        assert manifest["input_changed_during_read"] is False
        assert materialization._manifest_inputs_current(manifest, mod.activitywatch_event_index_input_files())

        main_stat = db.stat()
        append("second", 11)
        assert db.stat().st_mtime_ns == main_stat.st_mtime_ns
        assert not materialization._manifest_inputs_current(manifest, mod.activitywatch_event_index_input_files())

        refreshed = mod.materialize_activitywatch_event_index(
            root=tmp_path, start=date(2026, 6, 6), end=date(2026, 6, 7)
        )
        rows = Path(refreshed["product_paths"]["2026-06-06"]).read_text(encoding="utf-8").splitlines()
        assert [json.loads(row)["data"]["app"] for row in rows] == ["first", "second"]
        assert materialization._manifest_inputs_current(refreshed, mod.activitywatch_event_index_input_files())
    finally:
        writer.close()
