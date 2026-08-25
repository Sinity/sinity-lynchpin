from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from lynchpin.sources.activitywatch_models import AWEvent


def test_activitywatch_warm_run_does_not_read_or_rewrite_historical_days(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.ingest import activitywatch_materialize as materialize
    from lynchpin.materializers.partition_store import ArtifactStore, ProductPartitionKey

    db = tmp_path / "aw.db"
    db.write_bytes(b"stable")
    output = tmp_path / "events.ndjson"
    cfg = SimpleNamespace(activitywatch_db=db, activitywatch_archive_db_dir=tmp_path / "archive")
    history = AWEvent("aw-watcher-window_host", datetime(2026, 8, 20, 8, tzinfo=timezone.utc), datetime(2026, 8, 20, 9, tzinfo=timezone.utc), {"app": "history"})
    current = AWEvent("aw-watcher-window_host", datetime(2026, 8, 21, 8, tzinfo=timezone.utc), datetime(2026, 8, 21, 9, tzinfo=timezone.utc), {"app": "current"})
    monkeypatch.setattr(materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(materialize, "events_from_activitywatch_dbs", lambda *_args, **_kwargs: iter((history, current)))
    materialize.materialize_activitywatch_events(output=output)

    store = ArtifactStore(materialize.activitywatch_events_partition_store(output).root)
    historical = store.logical_partitions()[ProductPartitionKey.day("activitywatch.events", "2026-08-20")]
    historical_path = store.root / historical.path
    before = historical_path.stat()

    def unexpected_raw_read(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("warm materialization must use the input signature")

    monkeypatch.setattr(materialize, "events_from_activitywatch_dbs", unexpected_raw_read)
    materialize.materialize_activitywatch_events(output=output)

    after = historical_path.stat()
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns == before.st_mtime_ns


def test_activitywatch_one_day_correction_replaces_only_that_partition(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.ingest import activitywatch_materialize as materialize
    from lynchpin.materializers.partition_store import ArtifactStore, ProductPartitionKey

    db = tmp_path / "aw.db"
    db.write_bytes(b"stable")
    output = tmp_path / "events.ndjson"
    cfg = SimpleNamespace(activitywatch_db=db, activitywatch_archive_db_dir=tmp_path / "archive")
    old = (
        AWEvent("aw-watcher-window_host", datetime(2026, 8, 20, 8, tzinfo=timezone.utc), datetime(2026, 8, 20, 9, tzinfo=timezone.utc), {"app": "history"}),
        AWEvent("aw-watcher-window_host", datetime(2026, 8, 21, 8, tzinfo=timezone.utc), datetime(2026, 8, 21, 9, tzinfo=timezone.utc), {"app": "before"}),
    )
    monkeypatch.setattr(materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(materialize, "events_from_activitywatch_dbs", lambda *_args, **_kwargs: iter(old))
    materialize.materialize_activitywatch_events(output=output)
    store = ArtifactStore(materialize.activitywatch_events_partition_store(output).root)
    history_key = ProductPartitionKey.day("activitywatch.events", "2026-08-20")
    corrected_key = ProductPartitionKey.day("activitywatch.events", "2026-08-21")
    history_ref = store.logical_partitions()[history_key]
    corrected_ref = store.logical_partitions()[corrected_key]
    history_stat = (store.root / history_ref.path).stat()

    replacement = AWEvent("aw-watcher-window_host", datetime(2026, 8, 21, 8, tzinfo=timezone.utc), datetime(2026, 8, 21, 9, tzinfo=timezone.utc), {"app": "after"})
    monkeypatch.setattr(materialize, "events_from_activitywatch_dbs", lambda *_args, **_kwargs: iter((replacement,)))
    materialize.materialize_activitywatch_events(output=output, start=date(2026, 8, 21), end=date(2026, 8, 22))

    selected = ArtifactStore(materialize.activitywatch_events_partition_store(output).root).logical_partitions()
    assert selected[history_key].path == history_ref.path
    assert (store.root / history_ref.path).stat().st_mtime_ns == history_stat.st_mtime_ns
    assert selected[corrected_key].path != corrected_ref.path
    assert json.loads((store.root / selected[corrected_key].path).read_text())['data']['app'] == "after"


def test_activity_content_warm_run_uses_selected_days(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.ingest import activity_content_materialize as materialize
    from lynchpin.materializers.partition_store import ArtifactStore, ProductPartitionKey

    events = tmp_path / "events.ndjson"
    events.write_text("{}\n", encoding="utf-8")
    events.with_suffix(".manifest.json").write_text(json.dumps({"first_date": "2026-08-20", "last_date": "2026-08-21"}), encoding="utf-8")
    titles = tmp_path / "titles.ndjson"
    titles.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "daily.ndjson"
    spans = [
        SimpleNamespace(start=datetime(2026, 8, 20, 8, tzinfo=timezone.utc), end=datetime(2026, 8, 20, 9, tzinfo=timezone.utc), kind="focused", app="kitty", title="history", duration_s=3600.0),
        SimpleNamespace(start=datetime(2026, 8, 21, 8, tzinfo=timezone.utc), end=datetime(2026, 8, 21, 9, tzinfo=timezone.utc), kind="focused", app="kitty", title="current", duration_s=3600.0),
    ]
    monkeypatch.setattr(materialize, "canonical_activitywatch_events_path", lambda: events)
    monkeypatch.setattr(materialize, "activity_content_input_files", lambda: (events, titles))
    monkeypatch.setattr(materialize, "load_title_classification_map", lambda: {})
    monkeypatch.setattr(materialize, "classify_title_via_rules", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(materialize, "focus_spans", lambda **_kwargs: iter(spans))
    materialize.materialize_activity_content(output=output)

    store = ArtifactStore(output.with_name(".daily.partitions"))
    history = store.logical_partitions()[ProductPartitionKey.day("activity_content.daily", "2026-08-20")]
    history_stat = (store.root / history.path).stat()

    def unexpected_focus_read(**_kwargs: object) -> object:
        raise AssertionError("warm activity-content materialization must not rescan history")

    monkeypatch.setattr(materialize, "focus_spans", unexpected_focus_read)
    materialize.materialize_activity_content(output=output)
    assert (store.root / history.path).stat().st_mtime_ns == history_stat.st_mtime_ns
