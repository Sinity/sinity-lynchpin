from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lynchpin.ingest import webhistory


def test_live_profile_wal_visit_reaches_ordinary_history_reader(monkeypatch, tmp_path) -> None:
    from lynchpin.sources import chrome_profile, web

    profile = tmp_path / "chrome" / "Default"
    profile.mkdir(parents=True)
    history = profile / "History"
    conn = sqlite3.connect(history)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT); CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("INSERT INTO urls VALUES(1, 'https://wal.example/seen', 'WAL')")
    conn.execute("INSERT INTO visits VALUES(1, 1, 13400000000000000)")
    conn.commit()
    main_only = tmp_path / "main-only.sqlite"
    shutil.copy2(history, main_only)
    assert sqlite3.connect(main_only).execute("SELECT count(*) FROM visits").fetchone()[0] == 0
    monkeypatch.setattr(chrome_profile, "discover_profile_history_dbs", lambda: [(history, "chrome")])
    monkeypatch.setattr(webhistory, "discover_profile_history_dbs", lambda: [(history, "chrome")], raising=False)
    monkeypatch.setattr(webhistory, "_discover_browser_dbs", lambda: [])
    monkeypatch.setattr(webhistory, "_discover_manual_history_exports", lambda: [])
    monkeypatch.setattr(webhistory, "iter_chrome_history_batches", lambda: [])
    raw = tmp_path / "raw"
    reports = webhistory.extract_browser_data(raw_dir=raw)
    visits = list(web.iter_file_visits(next(raw.glob("*.ndjson"))))
    assert reports[0]["kind"] == "live_profile"
    assert [(v.url, v.title, v.source) for v in visits] == [("https://wal.example/seen", "WAL", "live_profile:chrome")]
    archived = tmp_path / "archived.ndjson"
    archived.write_text('{"iso_time":"2025-08-19T00:00:00+00:00","url":"https://archive.example/","source":"takeout:export"}\n')
    assert {v.source for v in [*visits, *web.iter_file_visits(archived)]} == {"live_profile:chrome", "takeout:export"}
    conn.close()


def test_live_profile_snapshot_failure_keeps_prior_raw_batch(monkeypatch, tmp_path) -> None:
    from lynchpin.sources import chrome_profile

    history = tmp_path / "History"
    history.write_bytes(b"invalid sqlite")
    prior = tmp_path / "raw" / "live_chrome_history_2026-01-01_to_2026-01-01.ndjson"
    prior.parent.mkdir()
    prior.write_text('{"iso_time":"2026-01-01T00:00:00+00:00","url":"https://prior.example/"}\n')
    monkeypatch.setattr(chrome_profile, "discover_profile_history_dbs", lambda: [(history, "chrome")])
    monkeypatch.setattr(webhistory, "_discover_browser_dbs", lambda: [])
    with __import__("pytest").raises(sqlite3.DatabaseError):
        webhistory.extract_browser_data(raw_dir=prior.parent)
    assert "prior.example" in prior.read_text()


def test_history_merge_collapses_one_visit_seen_by_two_carriers_and_keeps_provenance(tmp_path) -> None:
    # Anti-vacuity: fails (row_count 3) if the observation source is part of
    # the visit identity, and fails the count assertions if the manifest's
    # source-named counts are keyed by segment path instead of source.
    live, takeout = "live_profile:chrome-ws", "takeout_chrome:export.zip:Takeout/Chrome/History.json"
    data = tmp_path / "segments"
    data.mkdir()
    active = data / "active_unique_2026-05-01_to_2026-05-01.ndjson"
    archive = data / "archive_unique_2026-05-01_to_2026-05-01.ndjson"
    active.write_text(json.dumps({"iso_time": "2026-05-01T12:00:00+00:00", "url": "https://same.example/", "source": live}) + "\n")
    archive.write_text(
        json.dumps({"iso_time": "2026-05-01T12:00:04+00:00", "url": "https://same.example/", "source": takeout}) + "\n"
        + json.dumps({"iso_time": "2026-05-01T13:00:00+00:00", "url": "https://other.example/", "source": takeout}) + "\n"
    )
    output = tmp_path / "derived" / "full_history.ndjson"
    report = webhistory.build_full_history(data_dir=data, output=output)
    from lynchpin.sources.web import iter_file_visits

    assert report["row_count"] == 2
    assert [(row.url, row.source) for row in iter_file_visits(output)] == [
        ("https://same.example/", live),
        ("https://other.example/", takeout),
    ]
    assert report["source_counts"] == {live: 1, takeout: 1}
    assert report["input_source_counts"] == {live: 1, takeout: 2}
    assert report["source_duplicate_counts"] == {live: 0, takeout: 1}
    assert [row["path"] for row in report["segments"]] == [str(active), str(archive)]


def test_raw_batch_dedups_against_segments_that_record_a_legacy_path_source(tmp_path) -> None:
    # Anti-vacuity: fails (unique 2, duplicates 0) if the dedup identity
    # includes the source, because canonical segments written before native
    # tags were carried record the raw file path as their source.
    data = tmp_path / "data"
    data.mkdir()
    (data / "live_chrome-ws_history_2026-05-01_to_2026-05-01_unique_2026-05-01_to_2026-05-01.ndjson").write_text(
        json.dumps({
            "iso_time": "2026-05-01T12:00:00+00:00",
            "url": "https://seen.example/",
            "source": "/archive/gestalt/raw/live_chrome-ws_history_2026-05-01_to_2026-05-01.ndjson",
        }) + "\n"
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "live_chrome-ws_history_2026-05-01_to_2026-05-02.ndjson").write_text(
        json.dumps({"iso_time": "2026-05-01T12:00:05+00:00", "url": "https://seen.example/", "source": "live_profile:chrome-ws"}) + "\n"
        + json.dumps({"iso_time": "2026-05-02T09:00:00+00:00", "url": "https://new.example/", "source": "live_profile:chrome-ws"}) + "\n"
    )

    [report] = webhistory.dedup_raw_files(raw_dir=raw, data_dir=data, dry_run=True)

    assert (report["unique"], report["duplicates"]) == (1, 1)


def test_default_live_profile_keeps_its_recorded_label(monkeypatch, tmp_path) -> None:
    # Anti-vacuity: fails if the default profile is labelled
    # ``chrome-ws/Default``, which renames its ``live_profile:`` provenance
    # and ``live_<label>_history`` raw batches away from the recorded ones.
    from lynchpin.sources import chrome_profile

    histories = []
    for profile in ("Default", "Profile 1"):
        history = tmp_path / "chrome-ws" / profile / "History"
        history.parent.mkdir(parents=True)
        conn = sqlite3.connect(history)
        conn.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT); CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);")
        conn.execute("INSERT INTO urls VALUES(1, ?, 'T')", (f"https://{profile.replace(' ', '')}.example/",))
        conn.execute("INSERT INTO visits VALUES(1, 1, 13400000000000000)")
        conn.commit()
        conn.close()
        histories.append(history)
    monkeypatch.setenv(chrome_profile.CHROME_PROFILE_DBS_ENV, ":".join(str(path) for path in histories))
    monkeypatch.setattr(webhistory, "_discover_browser_dbs", lambda: [])
    monkeypatch.setattr(webhistory, "_discover_manual_history_exports", lambda: [])
    monkeypatch.setattr(webhistory, "iter_chrome_history_batches", lambda: [])

    assert [label for _path, label in chrome_profile.discover_profile_history_dbs()] == ["chrome-ws", "chrome-ws/Profile 1"]
    reports = webhistory.extract_browser_data(raw_dir=tmp_path / "raw")
    assert [Path(report["path"]).name.split("_20")[0] for report in reports] == ["live_chrome-ws_history", "live_chrome-ws_Profile 1_history"]
    from lynchpin.sources.web import iter_file_visits

    assert [v.source for v in iter_file_visits(Path(reports[0]["path"]))] == ["live_profile:chrome-ws"]


def test_build_full_history_streams_deduplicated_rows_in_order(monkeypatch, tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    segment = data_dir / "browser_unique_2026-01-01_to_2026-01-01.ndjson"
    segment.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "iso_time": "2026-01-01T12:00:10+00:00",
                        "url": "https://example.test/a",
                        "title": "Duplicate",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-01T11:00:00+00:00",
                        "url": "https://example.test/b",
                        "title": "Earlier",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-01T12:00:00+00:00",
                        "url": "https://example.test/a",
                        "title": "First",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "derived" / "full_history.ndjson"
    real_atomic_write_ndjson = webhistory.atomic_write_ndjson
    received_rows = []

    def capture_rows(path, rows, *, dumps=None) -> None:
        received_rows.append(rows)
        real_atomic_write_ndjson(path, rows, dumps=dumps)

    monkeypatch.setattr(webhistory, "atomic_write_ndjson", capture_rows)

    report = webhistory.build_full_history(data_dir=data_dir, output=output)

    assert len(received_rows) == 1
    assert not isinstance(received_rows[0], list)
    assert report["row_count"] == 2
    assert report["duplicate_count"] == 1
    assert output.read_text(encoding="utf-8") == (
        json.dumps(
            {
                "url": "https://example.test/b",
                "title": "Earlier",
                "norm": "https://example.test/b",
                "source": str(segment),
                "iso_time": "2026-01-01T11:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "url": "https://example.test/a",
                "title": "First",
                "norm": "https://example.test/a",
                "source": str(segment),
                "iso_time": "2026-01-01T12:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def test_build_full_history_publishes_empty_output(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    output = tmp_path / "derived" / "full_history.ndjson"
    output.parent.mkdir()
    output.write_text("stale\n", encoding="utf-8")

    report = webhistory.build_full_history(data_dir=data_dir, output=output)

    assert report["row_count"] == 0
    assert output.read_text(encoding="utf-8") == ""


def test_run_writes_success_receipt_with_retention_window(monkeypatch, tmp_path) -> None:
    started = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    _FixedDateTime.current = started
    monkeypatch.setattr(webhistory, "datetime", _FixedDateTime)
    monkeypatch.setattr(webhistory, "extract_browser_data", lambda **_kwargs: [])
    report_dir = tmp_path / "reports"

    report = webhistory.run(
        raw_dir=tmp_path / "raw",
        data_dir=tmp_path / "data",
        output=tmp_path / "derived" / "full_history.ndjson",
        report_dir=report_dir,
    )

    receipt = json.loads((report_dir / "last_run.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert receipt["status"] == "ok"
    assert receipt["chrome_retention_days"] == 90
    assert receipt["recoverable_window_start"] == "2026-06-02T10:00:00+00:00"


def test_schedule_status_surfaces_missed_and_failed_runs(tmp_path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    (report_dir / "last_run.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "finished_at": (now - timedelta(hours=49)).isoformat(),
                "recoverable_window_start": "2026-06-02T00:00:00+00:00",
                "recoverable_window_end": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    missed = webhistory.webhistory_schedule_status(report_dir=report_dir, now=now)
    assert missed["status"] == "missed"
    assert missed["recoverable_window_start"] == "2026-06-02T00:00:00+00:00"

    (report_dir / "last_run.json").write_text(
        json.dumps({"status": "failed", "finished_at": now.isoformat(), "error": "locked"}),
        encoding="utf-8",
    )
    failed = webhistory.webhistory_schedule_status(report_dir=report_dir, now=now)
    assert failed["status"] == "failed"
    assert failed["reason"] == "locked"


class _FixedDateTime(datetime):
    current: datetime = datetime.now(timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)
