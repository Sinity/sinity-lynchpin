import sqlite3
from datetime import date, datetime, timezone


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
        sqlite_path=db, lake_root=lake, tables=("gpu_sample",),
        now=datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
    )
    (result,) = results
    assert result.days_exported == ("2026-08-15", "2026-08-16")
    assert result.rows_exported == 5
    assert not (lake / "gpu_sample" / "dt=2026-08-17").exists()


def test_export_verifies_row_count_and_checksum_per_day(tmp_path):
    import duckdb

    from lynchpin.cli.machine_telemetry_export import run_export

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 4, "2026-08-16": 6})

    run_export(
        sqlite_path=db, lake_root=lake, tables=("gpu_sample", "hardware_state"),
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

    third = run_export(sqlite_path=db, lake_root=lake, tables=("gpu_sample",), now=now, full=True)
    assert third[0].days_exported == ("2026-08-15",)
    assert third[0].rows_exported == 3


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

    from lynchpin.cli.machine_telemetry_export import LakeVerificationError, export_table, verify_day_partition

    db = tmp_path / "telemetry.sqlite"
    lake = tmp_path / "lake"
    _seed_source_db(db, {"2026-08-15": 3})

    conn = duckdb.connect()
    conn.execute("INSTALL sqlite; LOAD sqlite;")
    conn.execute(f"ATTACH '{db}' AS src (TYPE SQLITE, READ_ONLY)")

    export_table(
        conn, table="gpu_sample", time_col="observed_at", lake_root=lake,
        today=date(2026, 8, 17),
    )
    part_dir = lake / "gpu_sample" / "dt=2026-08-15"

    # Sanity: the partition just written by export_table verifies clean.
    assert verify_day_partition(conn, table="gpu_sample", time_col="observed_at", day="2026-08-15", part_dir=part_dir) == 3

    # Simulate a write that landed short (e.g. a truncated COPY): drop one row
    # from the already-written partition without touching the source.
    conn.execute(
        f"COPY (SELECT * FROM read_parquet('{part_dir}/*.parquet') LIMIT 2) "
        f"TO '{part_dir}/part.parquet' (FORMAT PARQUET)"
    )

    with pytest.raises(LakeVerificationError):
        verify_day_partition(conn, table="gpu_sample", time_col="observed_at", day="2026-08-15", part_dir=part_dir)
