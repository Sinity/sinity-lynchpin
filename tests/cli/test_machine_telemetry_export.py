import json
import shutil
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


def _seed_source_db(path, rows_by_day):
    """rows_by_day: dict[str day] -> row count for gpu_sample, plus a same-day service_state row."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE gpu_sample (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              observed_at TEXT NOT NULL, host TEXT NOT NULL, boot_id TEXT,
              gpu_power_w REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE hardware_state (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              captured_at TEXT NOT NULL, host TEXT NOT NULL, boot_id TEXT,
              schema_version INTEGER NOT NULL, payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE source_status (
              source TEXT PRIMARY KEY,
              checked_at TEXT NOT NULL,
              status TEXT NOT NULL,
              reason TEXT,
              payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "INSERT INTO source_status (source, checked_at, status, reason, payload_json) "
            "VALUES ('gpu', '2026-08-17T00:00:00+00:00', 'ok', NULL, '{}')"
        )
        for day, n in rows_by_day.items():
            for hour in range(n):
                conn.execute(
                    "INSERT INTO gpu_sample (observed_at, host, boot_id, gpu_power_w) "
                    "VALUES (?, 'sinnix-prime', 'boot-a', ?)",
                    [f"{day}T{hour:02d}:00:00+00:00", 100.0 + hour],
                )
            conn.execute(
                "INSERT INTO hardware_state (captured_at, host, boot_id, schema_version, payload_json) "
                "VALUES (?, 'sinnix-prime', 'boot-a', 1, '{}')",
                [f"{day}T00:00:00+00:00"],
            )
        conn.commit()
    finally:
        conn.close()


def test_export_only_touches_sealed_days_before_today(tmp_path):
    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 3, "2026-08-16": 2, "2026-08-17": 5})

    results = run_export(
        sqlite_path=db,
        lake_root=lake,
        tables=("gpu_sample",),
        now=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
    )
    (result,) = results
    assert result.days_exported == ("2026-08-15", "2026-08-16")
    assert result.rows_exported == 5
    assert not (lake / "gpu_sample" / "dt=2026-08-17").exists()
    assert not list((lake / "gpu_sample").glob(".staging-*"))
    manifest = json.loads((lake / "manifest.json").read_text())
    assert manifest["schema"] == "sinnix-machine-telemetry-lake-v1"
    assert set(manifest["tables"]) == {
        "metric_sample",
        "service_state",
        "gpu_sample",
        "network_sample",
        "block_device_sample",
        "service_cgroup_io_sample",
        "service_cgroup_pressure_sample",
        "process_io_delta_sample",
        "process_memory_sample",
        "cgroup_memory_sample",
        "kill_event",
        "hardware_state",
        "source_status",
    }
    assert manifest["tables"]["source_status"]["metadata_path"] == (
        "source_status/metadata.parquet"
    )
    assert manifest["tables"]["source_status"]["row_count"] == 1
    assert manifest["tables"]["gpu_sample"]["sealed_days"] == [
        "2026-08-15",
        "2026-08-16",
    ]


def test_export_verifies_row_count_and_checksum_per_day(tmp_path):
    import duckdb

    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 4, "2026-08-16": 6})

    run_export(
        sqlite_path=db,
        lake_root=lake,
        tables=("gpu_sample", "hardware_state"),
        now=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
    )

    conn = duckdb.connect()
    pq_count, pq_sum = conn.execute(
        f"SELECT count(*), sum(id) FROM read_parquet('{lake}/gpu_sample/*/*.parquet')"
    ).fetchone()
    src = sqlite3.connect(str(db))
    src_count, src_sum = src.execute(
        "SELECT count(*), sum(id) FROM gpu_sample WHERE observed_at < '2026-08-17'"
    ).fetchone()
    src.close()
    assert pq_count == src_count == 10
    assert pq_sum == src_sum

    hw_count = conn.execute(
        f"SELECT count(*) FROM read_parquet('{lake}/hardware_state/*/*.parquet')"
    ).fetchone()[0]
    assert hw_count == 2  # one hardware_state row per seeded day


def test_export_is_idempotent_and_incremental_by_default(tmp_path):
    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 3})
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)

    first = run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now)
    assert first[0].days_exported == ("2026-08-15",)

    second = run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now)
    assert second[0].days_exported == ()  # nothing new to export

    third = run_export(
        sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now, full=True
    )
    assert third[0].days_exported == ("2026-08-15",)
    assert third[0].rows_exported == 3


def test_full_export_rebuilds_published_day_after_late_source_row(tmp_path):
    import duckdb
    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / "source.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 1})
    kwargs = dict(sqlite_path=db, lake_root=lake, tables=("gpu_sample",),
                  now=datetime(2026, 8, 17, tzinfo=timezone.utc))
    run_export(**kwargs)
    part = next((lake / "gpu_sample/dt=2026-08-15").glob("*.parquet"))
    with sqlite3.connect(db) as source:
        source.execute(
            "INSERT INTO gpu_sample (observed_at, host, boot_id, gpu_power_w) "
            "VALUES ('2026-08-15T12:00:00+00:00', 'fixture-host', 'fixture-boot', 250.0)"
        )
    from lynchpin.cli.machine_telemetry_export import LakeVerificationError

    preimage = part.read_bytes()
    with pytest.raises(LakeVerificationError):
        run_export(**kwargs)
    assert part.read_bytes() == preimage
    result = run_export(**kwargs, full=True)
    assert result[0].rows_exported == 2
    assert result[0].verified_days == 1
    with duckdb.connect() as conn:
        rows = conn.execute(
            "SELECT id, observed_at, host, boot_id, gpu_power_w "
            "FROM read_parquet(?) ORDER BY id", [str(part)]
        ).fetchall()
    with sqlite3.connect(db) as source:
        expected = source.execute(
            "SELECT id, observed_at, host, boot_id, gpu_power_w FROM gpu_sample ORDER BY id"
        ).fetchall()
    assert rows == expected
    assert run_export(**kwargs)[0].rows_exported == 0


def test_incremental_export_recovers_missing_partition(tmp_path):
    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 3, "2026-08-16": 2})
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now)
    shutil.rmtree(lake / "gpu_sample" / "dt=2026-08-15")

    (result,) = run_export(
        sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now
    )
    assert result.days_exported == ("2026-08-15",)
    assert result.rows_exported == 3
    assert (lake / "gpu_sample" / "dt=2026-08-15").is_dir()


def test_incremental_export_rejects_corrupt_existing_partition(tmp_path):
    """A present partition is re-verified instead of being trusted by name."""
    import duckdb
    import pytest

    from lynchpin.cli.machine_telemetry_export import LakeVerificationError, run_export

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 3})
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now)

    conn = duckdb.connect()
    part = next((lake / "gpu_sample" / "dt=2026-08-15").glob("*.parquet"))
    conn.execute(
        f"COPY (SELECT * FROM read_parquet('{part}') LIMIT 2) "
        f"TO '{part}' (FORMAT PARQUET)"
    )

    with pytest.raises(LakeVerificationError):
        run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now)


def test_export_raises_on_verification_mismatch(tmp_path):
    """A corrupted/incomplete parquet partition (count disagrees with source) is a hard error.

    Exercises the real production check in `export_table`: it re-reads the just-written
    parquet with an independent `read_parquet` query and compares count+checksum against
    the source SQLite row-for-row, catching exactly the class of bug this stage's design
    doc worries about (a partial or truncated COPY). Mutating this table's `WHERE ... != ?`
    verify comparison to `= ?` (silently accepting only rows that agree, instead of failing
    on disagreement) is what this test is guarding against.
    """
    import duckdb
    import pytest

    from lynchpin.cli.machine_telemetry_export import (
        LakeVerificationError,
        export_table,
        verify_exported_days,
    )

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 3})

    conn = duckdb.connect()
    conn.execute("INSTALL sqlite; LOAD sqlite;")
    conn.execute(f"ATTACH '{db}' AS src (TYPE SQLITE, READ_ONLY)")

    export_table(
        conn,
        table="gpu_sample",
        time_col="observed_at",
        lake_root=lake,
        today=date(2026, 8, 17),
    )
    out_dir = lake / "gpu_sample"
    part_dir = out_dir / "dt=2026-08-15"

    # Sanity: the partition just written by export_table verifies clean.
    assert verify_exported_days(
        conn,
        table="gpu_sample",
        time_col="observed_at",
        target_days=["2026-08-15"],
        out_dir=out_dir,
    ) == {"2026-08-15": 3}

    # Simulate a write that landed short (e.g. a truncated COPY): drop one row
    # from the already-written partition without touching the source.
    conn.execute(
        f"COPY (SELECT * FROM read_parquet('{part_dir}/*.parquet') LIMIT 2) "
        f"TO '{part_dir}/part.parquet' (FORMAT PARQUET)"
    )

    with pytest.raises(LakeVerificationError):
        verify_exported_days(
            conn,
            table="gpu_sample",
            time_col="observed_at",
            target_days=["2026-08-15"],
            out_dir=out_dir,
        )


@pytest.mark.parametrize("quoted_source,quoted_lake", [(True, False), (False, True), (True, True)])
def test_export_accepts_apostrophes_in_configured_paths(tmp_path, quoted_source, quoted_lake):
    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / ("source's.sqlite" if quoted_source else "source.sqlite")
    lake = tmp_path / ("lake's" if quoted_lake else "lake")
    _seed_source_db(db, {"2026-08-15": 2})
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    first = run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now)
    assert first[0].rows_exported == 2
    assert first[0].verified_days == 1
    assert (lake / "source_status" / "metadata.parquet").is_file()
    second = run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now)
    assert second[0].days_exported == ()
    full = run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now, full=True)
    assert full[0].rows_exported == 2
    assert json.loads((lake / "manifest.json").read_text())["source_database"] == str(db)


@pytest.mark.parametrize("change", ["value", "association", "null", "extra_column"])
@pytest.mark.parametrize("full", [False, True])
def test_export_rejects_changed_payload_with_unchanged_ids(tmp_path, monkeypatch, change, full):
    import duckdb
    from lynchpin.cli.machine_telemetry_export import LakeVerificationError, run_export, _sql_literal

    db = tmp_path / "source.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 2})
    with sqlite3.connect(db) as source:
        if change == "null":
            source.execute("UPDATE gpu_sample SET gpu_power_w = NULL WHERE id = 1")
        if change == "extra_column":
            source.execute('ALTER TABLE gpu_sample ADD COLUMN "extra""payload" TEXT')
            source.execute('UPDATE gpu_sample SET "extra""payload" = \'original\'')
    kwargs = dict(sqlite_path=db, lake_root=lake, tables=("gpu_sample",),
                  now=datetime(2026, 8, 17, tzinfo=timezone.utc))
    run_export(**kwargs)
    part = next((lake / "gpu_sample/dt=2026-08-15").glob("*.parquet"))
    changed = tmp_path / "changed.parquet"
    replacements = {
        "value": "gpu_power_w + 1 AS gpu_power_w",
        "association": "CASE WHEN id = 1 THEN 101.0 ELSE 100.0 END AS gpu_power_w",
        "null": "coalesce(gpu_power_w, 0.0) AS gpu_power_w",
        "extra_column": '\'changed\' AS "extra""payload"',
    }
    def corrupt_partition(path):
        with duckdb.connect() as conn:
            conn.execute(f"COPY (SELECT * REPLACE ({replacements[change]}) "
                         f"FROM read_parquet({_sql_literal(path)}, hive_partitioning=false)) "
                         f"TO {_sql_literal(changed)} (FORMAT PARQUET)")
        changed.replace(path)

    if full:
        from lynchpin.cli import machine_telemetry_export as exporter

        verify = exporter.verify_exported_days

        def verify_corrupted_stage(conn, **kwargs):
            # Full rebuilds may replace stale output, but must reject corruption
            # in the new stage without changing the previously published file.
            assert kwargs["out_dir"].name.startswith(".staging-")
            staged = next(kwargs["out_dir"].glob("dt=*/*.parquet"))
            corrupt_partition(staged)
            return verify(conn, **kwargs)

        monkeypatch.setattr(exporter, "verify_exported_days", verify_corrupted_stage)
    else:
        corrupt_partition(part)
    preimage = part.read_bytes()
    manifest_before = (lake / "manifest.json").read_bytes()
    with pytest.raises(LakeVerificationError):
        run_export(**kwargs, full=full)
    assert part.read_bytes() == preimage
    assert (lake / "manifest.json").read_bytes() == manifest_before


@pytest.mark.parametrize("change", ["null_reason", "extra_column"])
def test_metadata_payload_mismatch_preserves_published_file(tmp_path, change):
    import duckdb
    from lynchpin.cli.machine_telemetry_export import (
        LakeVerificationError, _export_metadata_table, _sql_literal,
    )

    db = tmp_path / "source.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {})
    with sqlite3.connect(db) as source:
        source.execute("ALTER TABLE source_status ADD COLUMN extra_payload TEXT DEFAULT 'original'")
    with duckdb.connect() as conn:
        conn.execute("LOAD sqlite")
        conn.execute(f"ATTACH {_sql_literal(db)} AS src (TYPE SQLITE, READ_ONLY)")
        _export_metadata_table(conn, table="source_status", lake_root=lake)
        final = lake / "source_status/metadata.parquet"
        preimage = final.read_bytes()

        class CorruptCopy:
            def execute(self, sql, *args):
                result = conn.execute(sql, *args)
                if sql.startswith("COPY"):
                    staged = next((lake / "source_status").glob(".staging-*/metadata.parquet"))
                    changed = tmp_path / "changed.parquet"
                    replacement = "'' AS reason" if change == "null_reason" else "'changed' AS extra_payload"
                    conn.execute(f"COPY (SELECT * REPLACE ({replacement}) "
                                 f"FROM read_parquet({_sql_literal(staged)})) "
                                 f"TO {_sql_literal(changed)} (FORMAT PARQUET)")
                    changed.replace(staged)
                return result

        with pytest.raises(LakeVerificationError):
            _export_metadata_table(CorruptCopy(), table="source_status", lake_root=lake)
        assert final.read_bytes() == preimage


@pytest.mark.parametrize("tables", [("unknown_fixture_table",), ("gpu_sample", "unknown_fixture_table")])
def test_unknown_export_selector_refuses_before_publishing(tmp_path, tables):
    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / "source.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 2})
    kwargs = dict(sqlite_path=db, lake_root=lake, now=datetime(2026, 8, 17, tzinfo=timezone.utc))
    run_export(**kwargs, tables=("gpu_sample",))
    before = {str(p.relative_to(lake)): p.read_bytes() for p in lake.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="unknown telemetry export tables"):
        run_export(**kwargs, tables=tables)
    after = {str(p.relative_to(lake)): p.read_bytes() for p in lake.rglob("*") if p.is_file()}
    assert after == before


def test_export_closes_job_bounded_connection_and_owns_spill(tmp_path, monkeypatch):
    import duckdb
    from lynchpin.cli import machine_telemetry_export as exporter

    db = tmp_path / "telemetry.sqlite"
    _seed_source_db(db, {"2026-08-15": 3})
    from lynchpin.substrate import connection

    original_connect = connection._bounded_connect
    original_temporary = exporter.tempfile.TemporaryDirectory
    connections = []
    spill = []

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        assert conn.execute("SELECT current_setting('memory_limit')").fetchone()[0] == "4.0 GiB"
        connections.append(conn)
        return conn

    def temporary(*args, **kwargs):
        directory = original_temporary(*args, **kwargs)
        spill.append(Path(directory.name))
        return directory

    monkeypatch.setattr(connection, "_bounded_connect", connect)
    monkeypatch.setattr(exporter.tempfile, "TemporaryDirectory", temporary)
    exporter.run_export(sqlite_path=db, lake_root=tmp_path / "lake", tables=("gpu_sample",),
                        now=datetime(2026, 8, 17, 12, tzinfo=timezone.utc))
    assert spill and all(not path.exists() for path in spill)
    with pytest.raises(duckdb.ConnectionException):
        connections[0].execute("SELECT 1")
