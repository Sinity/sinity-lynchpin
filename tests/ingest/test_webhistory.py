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


def test_merge_only_cli_uses_retained_merge_without_raw_extraction(monkeypatch, tmp_path):
    def unexpected_raw(**kwargs):
        raise AssertionError("merge-only must not extract raw inputs")

    observed = {}
    def merge(**kwargs):
        observed.update(kwargs)
        return {"row_count": 2}

    monkeypatch.setattr(webhistory, "run", unexpected_raw)
    monkeypatch.setattr(webhistory, "build_full_history", merge)
    output = tmp_path / "candidate.ndjson"
    assert webhistory.main(["--merge-only", "--output", str(output)]) == 0
    assert observed["output"] == output
    assert observed["start"] is None and observed["end"] is None


def test_candidate_merge_does_not_reimport_the_serving_carrier(monkeypatch, tmp_path):
    from types import SimpleNamespace

    segment = tmp_path / "browser_unique_2026-01-01_to_2026-01-01.ndjson"
    segment.write_text(json.dumps({"iso_time": "2026-01-01T12:00:00+00:00",
                                 "url": "https://example.com/neutral", "title": "neutral", "source": "fixture"})+"\n")
    old = tmp_path / "full_history.ndjson"
    old.write_text(json.dumps({"iso_time": "2010-01-01T12:00:00+00:00",
                              "url": "https://example.com/old", "title": "old", "source": "fixture"})+"\n")
    monkeypatch.setattr(webhistory, "get_config", lambda: SimpleNamespace(webhistory_ndjson=old))
    candidate = tmp_path / "candidate" / "full_history.ndjson"
    report = webhistory.build_full_history(data_dir=tmp_path, output=candidate)
    assert report["row_count"] == 1
    assert report["input_files"] == [str(segment)]
    assert old.exists()


def _bounded_history_fixture(monkeypatch, tmp_path):
    from datetime import date
    from types import SimpleNamespace
    from lynchpin.materializers import handlers
    from lynchpin.sources import chrome_profile

    cfg = SimpleNamespace(
        webhistory_raw_dir=tmp_path / "raw",
        webhistory_dir=tmp_path / "segments",
        webhistory_ndjson=tmp_path / "derived/full_history.ndjson",
        webhistory_report_dir=tmp_path / "reports",
    )
    cfg.webhistory_dir.mkdir()
    cfg.webhistory_raw_dir.mkdir()
    old = cfg.webhistory_dir / "archive_unique_2020-01-01_to_2026-05-02.ndjson"
    old.write_text(
        "".join(
            json.dumps({"iso_time": stamp, "url": url, "source": "takeout:retained"})
            + "\n"
            for stamp, url in (
                ("2020-01-01T12:00:00+00:00", "https://old.example/"),
                ("2026-05-02T12:00:00+00:00", "https://archive.example/recent"),
            )
        )
    )
    monkeypatch.setattr(webhistory, "get_config", lambda: cfg)
    monkeypatch.setattr(handlers, "get_config", lambda: cfg)
    webhistory.build_full_history(
        data_dir=cfg.webhistory_dir, output=cfg.webhistory_ndjson
    )
    db = tmp_path / "History"
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT,title TEXT); CREATE TABLE visits(id INTEGER PRIMARY KEY,url INTEGER,visit_time INTEGER);"
    )
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    for i, stamp in enumerate(
        (
            datetime(2020, 1, 1, 12, tzinfo=timezone.utc),
            datetime(2026, 5, 2, 13, tzinfo=timezone.utc),
        ),
        1,
    ):
        c.execute(
            "INSERT INTO urls VALUES(?,?,?)",
            (i, f"https://live.example/{i}", "Fixture"),
        )
        c.execute(
            "INSERT INTO visits VALUES(?,?,?)",
            (i, i, int((stamp - epoch).total_seconds() * 1e6)),
        )
    c.commit()
    c.close()
    monkeypatch.setattr(
        chrome_profile, "discover_profile_history_dbs", lambda: [(db, "fixture")]
    )
    monkeypatch.setattr(webhistory, "_discover_browser_dbs", lambda: [])
    monkeypatch.setattr(webhistory, "_discover_manual_history_exports", lambda: [])

    def archive_trap():
        raise AssertionError("bounded maintenance opened a historical Takeout archive")

    monkeypatch.setattr(webhistory, "iter_chrome_history_batches", archive_trap)
    return cfg, old, db, date(2026, 5, 1), date(2026, 5, 3)


def test_webhistory_production_tail_uses_live_sql_and_retained_archive_rows(
    monkeypatch, tmp_path
):
    from lynchpin.materializers import handlers
    from lynchpin.sources import browser_db, web

    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    old_bytes = old.read_bytes()
    original_connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if "FROM visits v" in sql:
                assert (
                    "WHERE" in sql
                    and "v.visit_time >=" in sql
                    and "v.visit_time <" in sql
                )
                assert len(parameters) == 2
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        browser_db.sqlite3,
        "connect",
        lambda *a, **kw: original_connect(*a, factory=ObservedConnection, **kw),
    )
    handlers._materialize_webhistory(start=start, end=end)
    assert {v.url for v in web.iter_file_visits(cfg.webhistory_ndjson)} == {
        "https://old.example/",
        "https://archive.example/recent",
        "https://live.example/2",
    }
    assert old.read_bytes() == old_bytes
    receipt = json.loads((cfg.webhistory_report_dir / "last_run.json").read_text())
    assert receipt["archive_acquisition"]["status"] == "not_admitted"
    assert [i["kind"] for i in receipt["extract"]["details"]] == ["live_profile"]


def test_webhistory_bounded_tail_refuses_changed_baseline_before_live_writes(
    monkeypatch, tmp_path
):
    from lynchpin.materializers import handlers
    from lynchpin.core.errors import MaterializationError
    import pytest

    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    before = cfg.webhistory_ndjson.read_bytes()
    manifest = webhistory.full_history_manifest_path(cfg.webhistory_ndjson)
    manifest_before = manifest.read_bytes()
    old.write_text(old.read_text() + "\n")
    with pytest.raises(MaterializationError, match="canonical"):
        handlers._materialize_webhistory(start=start, end=end)
    assert cfg.webhistory_ndjson.read_bytes() == before
    assert manifest.read_bytes() == manifest_before
    assert list(cfg.webhistory_raw_dir.iterdir()) == []


def test_webhistory_bounded_tail_never_discovers_historical_inputs(
    monkeypatch, tmp_path
):
    from lynchpin.materializers import handlers

    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)

    def forbidden():
        raise AssertionError("historical acquisition is not admitted by maintenance")

    monkeypatch.setattr(webhistory, "_discover_browser_dbs", forbidden)
    monkeypatch.setattr(webhistory, "_discover_manual_history_exports", forbidden)
    handlers._materialize_webhistory(start=start, end=end)


def test_webhistory_repeated_same_day_tail_preserves_prior_unique_segment(
    monkeypatch, tmp_path
):
    from lynchpin.materializers import handlers
    from lynchpin.sources.web import iter_file_visits

    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    handlers._materialize_webhistory(start=start, end=end)
    before = {p: p.read_bytes() for p in cfg.webhistory_dir.iterdir()}
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO urls VALUES(3,'https://live.example/3','Later')")
        stamp = datetime(2026, 5, 2, 14, tzinfo=timezone.utc)
        micros = int(
            (stamp - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1e6
        )
        conn.execute("INSERT INTO visits VALUES(3,3,?)", (micros,))
    handlers._materialize_webhistory(start=start, end=end)
    assert all(p.read_bytes() == value for p, value in before.items())
    assert {v.url for v in iter_file_visits(cfg.webhistory_ndjson)} == {
        "https://old.example/",
        "https://archive.example/recent",
        "https://live.example/2",
        "https://live.example/3",
    }


def test_webhistory_invalid_merged_base_refuses_before_live_writes(
    monkeypatch, tmp_path
):
    import pytest
    from lynchpin.core.errors import MaterializationError
    from lynchpin.materializers import handlers

    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    cfg.webhistory_ndjson.write_text("{damaged\n")
    manifest = webhistory.full_history_manifest_path(cfg.webhistory_ndjson)
    before = cfg.webhistory_ndjson.read_bytes(), manifest.read_bytes()
    with pytest.raises(MaterializationError, match="canonical"):
        handlers._materialize_webhistory(start=start, end=end)
    assert (cfg.webhistory_ndjson.read_bytes(), manifest.read_bytes()) == before
    assert list(cfg.webhistory_raw_dir.iterdir()) == []


def test_webhistory_live_read_failure_preserves_canonical_pair(monkeypatch, tmp_path):
    import pytest
    from lynchpin.materializers import handlers

    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    manifest = webhistory.full_history_manifest_path(cfg.webhistory_ndjson)
    before = cfg.webhistory_ndjson.read_bytes(), manifest.read_bytes()
    db.write_bytes(b"broken SQLite")
    with pytest.raises(sqlite3.DatabaseError):
        handlers._materialize_webhistory(start=start, end=end)
    assert (cfg.webhistory_ndjson.read_bytes(), manifest.read_bytes()) == before
    assert list(cfg.webhistory_raw_dir.iterdir()) == []


def test_webhistory_browser_sql_timestamp_bounds_are_exact_and_half_open(tmp_path):
    from zoneinfo import ZoneInfo
    from lynchpin.sources.browser_db import iter_browser_db_visits

    for kind in ("chromium", "firefox"):
        db = tmp_path / kind
        epoch = datetime(
            1601 if kind == "chromium" else 1970, 1, 1, tzinfo=timezone.utc
        )
        start = datetime(2030, 3, 30, 6, tzinfo=ZoneInfo("Europe/Warsaw"))
        end = datetime(2030, 4, 1, 6, tzinfo=ZoneInfo("Europe/Warsaw"))

        def micros(value):
            d = value.astimezone(timezone.utc) - epoch
            return (d.days * 86400 + d.seconds) * 1_000_000 + d.microseconds

        lower, upper = micros(start), micros(end)
        with sqlite3.connect(db) as conn:
            if kind == "chromium":
                conn.executescript(
                    "CREATE TABLE urls(id INTEGER PRIMARY KEY,url TEXT,title TEXT);CREATE TABLE visits(id INTEGER PRIMARY KEY,url INTEGER,visit_time INTEGER);"
                )
                urls, visits = "urls", "visits"
            else:
                conn.executescript(
                    "CREATE TABLE moz_places(id INTEGER PRIMARY KEY,url TEXT,title TEXT);CREATE TABLE moz_historyvisits(id INTEGER PRIMARY KEY,place_id INTEGER,visit_date INTEGER);"
                )
                urls, visits = "moz_places", "moz_historyvisits"
            for i, t in enumerate((lower - 1, lower, upper - 1, upper), 1):
                conn.execute(
                    f"INSERT INTO {urls} VALUES(?,?,?)",
                    (i, f"https://boundary.example/{i}", "Fixture"),
                )
                conn.execute(f"INSERT INTO {visits} VALUES(?,?,?)", (i, i, t))
        assert [
            v.url for v in iter_browser_db_visits(db, kind=kind, start=start, end=end)
        ] == ["https://boundary.example/2", "https://boundary.example/3"]


def test_webhistory_changed_source_during_merge_preserves_published_pair(monkeypatch, tmp_path):
    import pytest
    from lynchpin.core.errors import MaterializationError
    from lynchpin.materializers import handlers
    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    manifest = webhistory.full_history_manifest_path(cfg.webhistory_ndjson)
    before = cfg.webhistory_ndjson.read_bytes(), manifest.read_bytes()
    publish = webhistory.atomic_write_ndjson
    def mutate_before_publication(path, rows, **kwargs):
        if path == cfg.webhistory_ndjson:
            old.write_bytes(old.read_bytes() + b"\n")
        return publish(path, rows, **kwargs)
    monkeypatch.setattr(webhistory, "atomic_write_ndjson", mutate_before_publication)
    with pytest.raises(MaterializationError, match="source segments changed"):
        handlers._materialize_webhistory(start=start, end=end)
    assert (cfg.webhistory_ndjson.read_bytes(), manifest.read_bytes()) == before


def test_webhistory_explicit_unbounded_import_keeps_archive_and_historical_routes(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from lynchpin.materializers import handlers
    from lynchpin.sources.web import WebHistoryVisit, iter_file_visits
    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    opened = []
    def archives():
        opened.append('archive')
        yield SimpleNamespace(
            archive=tmp_path/'takeout-archive.tgz', member='Takeout/Chrome/History.json',
            visits=(WebHistoryVisit(timestamp=datetime(2021,1,1,12,tzinfo=timezone.utc),
                                    url='https://explicit-archive.example/', title='Archive', source='takeout:explicit'),),
        )
    monkeypatch.setattr(webhistory, 'iter_chrome_history_batches', archives)
    monkeypatch.setattr(webhistory, '_discover_browser_dbs', lambda: [(db,'chromium','historical:fixture')])
    handlers._materialize_webhistory()
    assert opened == ['archive']
    urls = {v.url for v in iter_file_visits(cfg.webhistory_ndjson)}
    assert {'https://explicit-archive.example/', 'https://live.example/1',
            'https://live.example/2', 'https://old.example/', 'https://archive.example/recent'} <= urls
    receipt = json.loads((cfg.webhistory_report_dir/'last_run.json').read_text())
    assert receipt['archive_acquisition']['status'] == 'explicit_import'
    assert {row['kind'] for row in receipt['extract']['details']} == {'chromium','live_profile','takeout_chrome_archive'}


def test_webhistory_bounded_dry_run_does_not_read_retained_raw(monkeypatch, tmp_path):
    cfg, old, db, start, end = _bounded_history_fixture(monkeypatch, tmp_path)
    stale_raw = cfg.webhistory_raw_dir / "live_fixture_history_2026-05-02_to_2026-05-02.ndjson"
    stale_raw.write_text("{invalid old extraction\n")
    manifest = webhistory.full_history_manifest_path(cfg.webhistory_ndjson)
    before = {p: p.read_bytes() for p in (cfg.webhistory_ndjson, manifest, old, stale_raw)}
    report = webhistory.run(start=start, end=end, dry_run=True)
    assert report["dry_run"] is True
    assert report["dedup"]["details"] == []
    assert all(p.read_bytes() == value for p, value in before.items())
    assert list(cfg.webhistory_raw_dir.iterdir()) == [stale_raw]
