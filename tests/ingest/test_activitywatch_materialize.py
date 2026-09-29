from __future__ import annotations

import json
import sqlite3
import pytest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources.activitywatch_models import AWEvent


def _aw_fixture(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.sources import activitywatch_raw

    db = tmp_path / "aw.db"
    cfg = SimpleNamespace(activitywatch_db=db, activitywatch_archive_db_dir=tmp_path / "archive")
    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_raw, "get_config", lambda: cfg)
    return db, tmp_path / "events.ndjson"


def _create_aw_db(db, *, wal: bool = True) -> sqlite3.Connection:
    writer = sqlite3.connect(db)
    if wal:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE buckets (id INTEGER PRIMARY KEY, name TEXT)")
    writer.execute("CREATE TABLE events (bucketrow INTEGER, starttime INTEGER, endtime INTEGER, data TEXT)")
    writer.execute("INSERT INTO buckets VALUES (1, 'aw-watcher-window_host')")
    writer.commit()
    return writer


def _append_aw_event(writer: sqlite3.Connection, app: str, hour: int) -> None:
    start = int(datetime(2026, 1, 1, hour, tzinfo=timezone.utc).timestamp()) * 10**9
    writer.execute("INSERT INTO events VALUES (1, ?, ?, ?)", (start, start + 60 * 10**9, json.dumps({"app": app})))
    writer.commit()


def _apps(output) -> list[str]:
    return [json.loads(line)["data"]["app"] for line in output.read_text().splitlines()]


def _published_state(output) -> dict[str, bytes]:
    return {
        str(path.relative_to(output.parent)): path.read_bytes()
        for path in sorted(output.parent.rglob("*"))
        if path.is_file() and not path.name.startswith("aw.db") and "archive" not in path.parts
    }


def test_materialize_activitywatch_events_sees_committed_wal_append(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.sources import activitywatch_raw

    db, output = _aw_fixture(monkeypatch, tmp_path)
    writer = _create_aw_db(db)
    try:
        _append_aw_event(writer, "first", 10)
        first = activitywatch_materialize.materialize_activitywatch_events(output=output)
        assert first["input_changed_during_read"] is False
        main_stat = db.stat()
        _append_aw_event(writer, "second", 11)
        assert db.stat().st_size == main_stat.st_size
        assert db.stat().st_mtime_ns == main_stat.st_mtime_ns
        assert (tmp_path / "aw.db-wal").exists()

        # The committed WAL frame is a new input identity, not a reusable one.
        second = activitywatch_materialize.materialize_activitywatch_events(output=output)
        assert _apps(output) == ["first", "second"]
        assert second["row_count"] == 2
        assert second["input_signature"] != first["input_signature"]
        store = activitywatch_materialize.activitywatch_events_partition_store(output)
        assert store.metadata["input_signature"] == second["input_signature"]

        def unexpected_read(*_args, **_kwargs):
            raise AssertionError("unchanged WAL input should reuse the published product")

        monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", unexpected_read)
        unchanged = activitywatch_materialize.materialize_activitywatch_events(output=output)
        assert unchanged["row_count"] == second["row_count"]
        assert unchanged["input_signature"] == second["input_signature"]
    finally:
        writer.close()

    # Closing the writer checkpoints WAL into the main database. The selected
    # rows remain identical across that identity transition.
    assert not (tmp_path / "aw.db-wal").exists()
    monkeypatch.setattr(
        activitywatch_materialize,
        "events_from_activitywatch_dbs",
        activitywatch_raw.events_from_activitywatch_dbs,
    )
    activitywatch_materialize.materialize_activitywatch_events(output=output)
    assert _apps(output) == ["first", "second"]


def test_activitywatch_publication_names_state_read_when_writer_commits_during_read(monkeypatch, tmp_path):
    from lynchpin.core.cache import input_versions
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.sources import activitywatch_raw

    db, output = _aw_fixture(monkeypatch, tmp_path)
    writer = _create_aw_db(db)
    try:
        _append_aw_event(writer, "first", 10)
        _append_aw_event(writer, "second", 11)
        before_read = input_versions((db,))
        real_reader = activitywatch_raw.events_from_activitywatch_dbs

        def racing_reader(*args, **kwargs):
            events = real_reader(*args, **kwargs)
            head = next(events)
            # The SELECT's read transaction is open; this commit lands in WAL.
            _append_aw_event(writer, "late", 12)
            yield head
            yield from events

        monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", racing_reader)
        raced = activitywatch_materialize.materialize_activitywatch_events(output=output)

        # The rows are the snapshot the statement read, and the published
        # identity is the pre-read observation, flagged as overtaken.
        assert _apps(output) == ["first", "second"]
        assert raced["input_versions"] == before_read
        assert raced["input_versions"] != input_versions((db,))
        assert raced["input_changed_during_read"] is True
        store = activitywatch_materialize.activitywatch_events_partition_store(output)
        assert store.metadata["input_signature"] is None

        monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", real_reader)
        caught_up = activitywatch_materialize.materialize_activitywatch_events(output=output)
        assert _apps(output) == ["first", "second", "late"]
        assert caught_up["input_changed_during_read"] is False
        assert caught_up["input_versions"] == input_versions((db,))
    finally:
        writer.close()


def test_activitywatch_locked_database_preserves_last_product(monkeypatch, tmp_path):
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.sources import activitywatch_raw

    db, output = _aw_fixture(monkeypatch, tmp_path)
    writer = _create_aw_db(db, wal=False)
    _append_aw_event(writer, "first", 10)
    activitywatch_materialize.materialize_activitywatch_events(output=output)
    published = _published_state(output)

    _append_aw_event(writer, "second", 11)
    real_connect = activitywatch_raw._connect
    monkeypatch.setattr(activitywatch_raw, "_connect", lambda path=None: real_connect(path, timeout=0))
    writer.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(SourceUnavailableError, match="locked"):
            activitywatch_materialize.materialize_activitywatch_events(output=output)
    finally:
        writer.rollback()
        writer.close()

    assert _published_state(output) == published
    assert _apps(output) == ["first"]


def test_activitywatch_read_leaves_uncheckpointed_wal_in_place(monkeypatch, tmp_path):
    import shutil

    from lynchpin.ingest import activitywatch_materialize

    db, output = _aw_fixture(monkeypatch, tmp_path)
    live = tmp_path / "live"
    live.mkdir()
    writer = _create_aw_db(live / "aw.db")
    try:
        _append_aw_event(writer, "first", 10)
        # Copy while the writer holds the WAL: the state a crashed writer leaves.
        for name in ("aw.db", "aw.db-wal", "aw.db-shm"):
            shutil.copy2(live / name, tmp_path / name)
    finally:
        writer.close()
    originals = {name: (tmp_path / name).read_bytes() for name in ("aw.db", "aw.db-wal")}

    manifest = activitywatch_materialize.materialize_activitywatch_events(output=output)

    assert _apps(output) == ["first"]
    assert manifest["input_changed_during_read"] is False
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals


def test_activitywatch_missing_database_is_unavailable_not_empty(monkeypatch, tmp_path):
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.ingest import activitywatch_materialize

    db, output = _aw_fixture(monkeypatch, tmp_path)
    with pytest.raises(SourceUnavailableError, match="no live or archived"):
        activitywatch_materialize.materialize_activitywatch_events(output=output)
    assert not output.exists()

    writer = _create_aw_db(db, wal=False)
    _append_aw_event(writer, "first", 10)
    writer.close()
    activitywatch_materialize.materialize_activitywatch_events(output=output)
    published = _published_state(output)

    # An archive alone does not stand in for the live database the last
    # product consumed.
    archive = tmp_path / "archive"
    archive.mkdir()
    db.rename(archive / "old.db")
    with pytest.raises(SourceUnavailableError, match="live database consumed by the last product"):
        activitywatch_materialize.materialize_activitywatch_events(output=output)
    assert _published_state(output) == published
    assert (archive / "old.db").exists()


def test_activitywatch_observed_empty_database_publishes_empty_product(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize

    db, output = _aw_fixture(monkeypatch, tmp_path)
    _create_aw_db(db, wal=False).close()

    manifest = activitywatch_materialize.materialize_activitywatch_events(output=output)
    assert manifest["row_count"] == 0
    assert manifest["covered_dates"] == []
    assert manifest["input_file_count"] == 1
    assert manifest["input_changed_during_read"] is False

    monkeypatch.setattr(
        activitywatch_materialize,
        "events_from_activitywatch_dbs",
        lambda *_args, **_kwargs: pytest.fail("an unchanged empty source should reuse its product"),
    )
    assert activitywatch_materialize.materialize_activitywatch_events(output=output)["row_count"] == 0


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
        lambda _prefix, **_kwargs: iter([event]),
    )

    manifest = activitywatch_materialize.materialize_activitywatch_events(output=output)

    assert manifest["row_count"] == 1
    assert manifest["schema_version"] == ACTIVITYWATCH_EVENTS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None


def test_materialize_activitywatch_events_reports_logical_date_bounds(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize

    output = tmp_path / "events.ndjson"
    db = tmp_path / "aw.db"
    db.write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(activitywatch_db=db, activitywatch_archive_db_dir=tmp_path / "archive")
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
        lambda _prefix, **_kwargs: iter([event]),
    )

    manifest = activitywatch_materialize.materialize_activitywatch_events(output=output)

    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-01"
    assert manifest["first_timestamp_date"] == "2026-01-02"
    assert manifest["last_timestamp_date"] == "2026-01-02"
    assert manifest["date_boundary"] == "logical_06:00_local"


def test_activitywatch_incremental_tail_does_not_read_or_rewrite_history(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize

    output = tmp_path / "events.ndjson"
    db = tmp_path / "aw.db"
    db.write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(activitywatch_db=db, activitywatch_archive_db_dir=tmp_path / "archive")
    initial = AWEvent(
        bucket="aw-watcher-window_host",
        start=datetime(2026, 6, 5, 8, tzinfo=timezone.utc),
        end=datetime(2026, 6, 5, 9, tzinfo=timezone.utc),
        data={"app": "history"},
    )
    tail = AWEvent(
        bucket="aw-watcher-window_host",
        start=datetime(2026, 6, 7, 8, tzinfo=timezone.utc),
        end=datetime(2026, 6, 7, 9, tzinfo=timezone.utc),
        data={"app": "tail"},
    )
    events = iter([initial])
    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", lambda *_args, **_kwargs: events)
    activitywatch_materialize.materialize_activitywatch_events(output=output)

    monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", lambda *_args, **_kwargs: iter([tail]))
    manifest = activitywatch_materialize.materialize_activitywatch_events(
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 8),
    )

    assert [json.loads(line)["data"]["app"] for line in output.read_text(encoding="utf-8").splitlines()] == ["history", "tail"]
    assert manifest["row_count"] == 2
    assert manifest["row_order"] == "logical_date"


def test_activitywatch_tail_clone_failure_keeps_serving_carrier(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.ingest._manifest import (
        atomic_write_indexed_ndjson,
        replace_indexed_ndjson_tail,
    )

    output = tmp_path / "events.ndjson"
    rows = [{
        "bucket": "aw-watcher-window_host",
        "start": "2026-06-05T08:00:00+00:00",
        "end": "2026-06-05T09:00:00+00:00",
        "data": {"app": "serving"},
    }]
    offsets = atomic_write_indexed_ndjson(
        output,
        rows,
        date_getter=lambda row: date.fromisoformat(row["start"][:10]),
    )
    before = output.read_bytes()

    def fail_clone(*_args, **_kwargs):
        raise OSError(95, "reflink unavailable")

    monkeypatch.setattr("lynchpin.ingest._manifest.fcntl.ioctl", fail_clone)
    with pytest.raises(activitywatch_materialize.MaterializationError, match="copy-on-write"):
        replace_indexed_ndjson_tail(
            output,
            [{
                "bucket": "aw-watcher-window_host",
                "start": "2026-06-06T08:00:00+00:00",
                "end": "2026-06-06T09:00:00+00:00",
                "data": {"app": "new"},
            }],
            start=date(2026, 6, 6),
            date_getter=lambda row: date.fromisoformat(row["start"][:10]),
            offsets=offsets,
        )
    assert output.read_bytes() == before
    assert not output.with_name(f".{output.name}.tmp").exists()


def test_materialize_activitywatch_events_replaces_only_requested_window(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.ingest._manifest import atomic_write_indexed_ndjson

    output = tmp_path / "events.ndjson"
    (tmp_path / "aw.db").write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(activitywatch_db=tmp_path / "aw.db", activitywatch_archive_db_dir=tmp_path / "archive")
    rows = [
        json.dumps(
            {
                "bucket": "aw-watcher-window_host",
                "start": "2026-06-05T08:00:00+00:00",
                "end": "2026-06-05T09:00:00+00:00",
                "data": {"app": "before"},
            }
        )
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    indexed_rows = [json.loads(row) for row in rows]
    offsets = atomic_write_indexed_ndjson(
        output,
        indexed_rows,
        date_getter=lambda row: date.fromisoformat(row["start"][:10]),
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps({
            "row_order": "logical_date",
            "row_offsets": offsets,
            "last_date": "2026-06-05",
            "row_count": len(indexed_rows),
            "row_counts": {"2026-06-05": 1},
        }),
        encoding="utf-8",
    )
    replacement = AWEvent(
        bucket="aw-watcher-window_host",
        start=datetime(2026, 6, 7, 8, tzinfo=timezone.utc),
        end=datetime(2026, 6, 7, 8, 30, tzinfo=timezone.utc),
        data={"app": "new-tail"},
    )
    calls: list[tuple[object, datetime | None, datetime | None]] = []

    def fake_events(prefix, *, start=None, end=None, **_kwargs):
        calls.append((prefix, start, end))
        assert prefix == activitywatch_materialize.BUCKET_PREFIXES
        return iter([replacement])

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", fake_events)

    manifest = activitywatch_materialize.materialize_activitywatch_events(
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 8),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    apps = {row["data"]["app"] for row in rows}
    assert apps == {"before", "new-tail"}
    assert manifest["window_start"] == "2026-06-06"
    assert manifest["window_end"] == "2026-06-08"
    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["covered_date_count"] == 3
    assert len(calls) == 1
    assert all(call[1] is not None and call[2] is not None for call in calls)


def test_materialize_activitywatch_events_rejects_unindexed_incremental_carrier(
    monkeypatch, tmp_path
):
    from lynchpin.ingest import activitywatch_materialize

    output = tmp_path / "events.ndjson"
    (tmp_path / "aw.db").write_text("fixture", encoding="utf-8")
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
                '": "aw-watcher-window_host", "data": {"app": "truncated"}',
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

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(
        activitywatch_materialize,
        "events_from_activitywatch_dbs",
        lambda prefix, *, start=None, end=None, **_kwargs: iter([replacement]),
    )

    with pytest.raises(activitywatch_materialize.MaterializationError, match="indexed append-compatible tail"):
        activitywatch_materialize.materialize_activitywatch_events(
            output=output,
            start=date(2026, 6, 6),
            end=date(2026, 6, 7),
        )


def test_materialize_activitywatch_events_records_zero_row_window_days(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.ingest._manifest import atomic_write_indexed_ndjson

    output = tmp_path / "events.ndjson"
    (tmp_path / "aw.db").write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(activitywatch_db=tmp_path / "aw.db", activitywatch_archive_db_dir=tmp_path / "archive")
    indexed_rows = [{
        "bucket": "aw-watcher-window_host",
        "start": "2026-06-05T08:00:00+00:00",
        "end": "2026-06-05T09:00:00+00:00",
        "data": {"app": "before"},
    }]
    offsets = atomic_write_indexed_ndjson(
        output,
        indexed_rows,
        date_getter=lambda row: date.fromisoformat(row["start"][:10]),
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps({
            "row_order": "logical_date",
            "row_offsets": offsets,
            "covered_dates": ["2026-06-05"],
            "last_date": "2026-06-05",
            "row_count": 1,
            "row_counts": {"2026-06-05": 1},
        }),
        encoding="utf-8",
    )

    def fake_events(prefix, *, start=None, end=None, **_kwargs):
        return iter(())

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", fake_events)

    manifest = activitywatch_materialize.materialize_activitywatch_events(
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 8),
    )

    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]


def test_materialize_activitywatch_events_purges_phantom_covered_dates(monkeypatch, tmp_path):
    """Regression test for lynchpin-jzb.

    A manifest carrying stale placeholder/bad-merge covered_dates (e.g. a
    default 2010-01-01 date and a false 2017-2018 span) with zero backing
    events must not have that claim re-affirmed on the next incremental
    materialization run just because the run's own window lies elsewhere.
    """
    from lynchpin.ingest import activitywatch_materialize
    from lynchpin.ingest._manifest import atomic_write_indexed_ndjson

    output = tmp_path / "events.ndjson"
    (tmp_path / "aw.db").write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(activitywatch_db=tmp_path / "aw.db", activitywatch_archive_db_dir=tmp_path / "archive")
    indexed_rows = [{
        "bucket": "aw-watcher-window_host",
        "start": "2026-06-05T08:00:00+00:00",
        "end": "2026-06-05T09:00:00+00:00",
        "data": {"app": "real"},
    }]
    offsets = atomic_write_indexed_ndjson(
        output,
        indexed_rows,
        date_getter=lambda row: date.fromisoformat(row["start"][:10]),
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps(
                {
                    "row_order": "logical_date",
                    "row_offsets": offsets,
                    "first_date": "2010-01-01",
                "last_date": "2026-06-05",
                "covered_dates": ["2010-01-01", "2010-01-02", "2017-01-30", "2026-06-05"],
            }
        ),
        encoding="utf-8",
    )

    event = AWEvent(
        bucket="aw-watcher-window_host",
        start=datetime(2026, 6, 6, 10, tzinfo=timezone.utc),
        end=datetime(2026, 6, 6, 10, 5, tzinfo=timezone.utc),
        data={"app": "kitty"},
    )

    monkeypatch.setattr(activitywatch_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(activitywatch_materialize, "events_from_activitywatch_dbs", lambda _prefix, **_kw: iter([event]))

    manifest = activitywatch_materialize.materialize_activitywatch_events(
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
    )

    assert "2010-01-01" not in manifest["covered_dates"]
    assert "2010-01-02" not in manifest["covered_dates"]
    assert "2017-01-30" not in manifest["covered_dates"]
    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06"]
    assert manifest["first_date"] == "2026-06-05"
