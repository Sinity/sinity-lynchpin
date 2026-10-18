"""Tiny file-set contracts; no live SQLite history is inspected."""

from types import SimpleNamespace
import json

import pytest

from lynchpin.core.errors import SourceUnavailableError
from lynchpin.core.input_snapshot import input_snapshot
from lynchpin.sources.machine_segments import (
    SCHEMA,
    manifest_path,
    resolve_machine_sources,
)
from lynchpin.ingest.machine_materialize import machine_input_files


def entry(path, identity):
    st = path.stat()
    return {
        "id": identity,
        "file": path.name,
        "identity": dict(
            inode=st.st_ino,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            ctime_ns=st.st_ctime_ns,
        ),
    }


def source_set(tmp_path):
    current, legacy = tmp_path / "current.sqlite", tmp_path / "legacy.sqlite"
    current.write_bytes(b"current")
    legacy.write_bytes(b"retained history")
    payload = {
        "schema": SCHEMA,
        "state": "ready",
        "generation": "generation-a",
        "origin": {
            "path": str(current.resolve()),
            "device": legacy.stat().st_dev,
            "inode": legacy.stat().st_ino,
        },
        "sealed": [entry(legacy, "legacy-a")],
        "current": entry(current, "current-a"),
    }
    current.with_name(current.name + ".segments.lock").touch()
    manifest_path(current).write_text(json.dumps(payload))
    return current, legacy, payload


def test_unsegmented_source_remains_supported_and_wal_is_an_input(tmp_path):
    current = tmp_path / "current.sqlite"
    assert not resolve_machine_sources(current).segments
    current.write_bytes(b"current")
    cfg = SimpleNamespace(machine_telemetry_db=current, local_root=tmp_path / "local")
    assert machine_input_files(cfg) == (current,)
    wal = current.with_name(current.name + "-wal")
    wal.write_bytes(b"new committed rows")
    assert machine_input_files(cfg) == (current, wal)
    wal.unlink()
    assert machine_input_files(cfg) == (current,)


def test_required_legacy_current_manifest_and_wal_are_fingerprinted(tmp_path):
    current, legacy, _ = source_set(tmp_path)
    wal = current.with_name(current.name + "-wal")
    wal.write_bytes(b"tail")
    source = resolve_machine_sources(current)
    assert source.generation == "generation-a"
    assert source.input_files() == (manifest_path(current), legacy, current, wal)
    legacy.unlink()
    with pytest.raises(SourceUnavailableError):
        resolve_machine_sources(current)


@pytest.mark.parametrize(
    "kind",
    [
        "transition",
        "bad-json",
        "duplicate-id",
        "duplicate-file",
        "escape",
        "wrong-current",
        "bad-identity",
    ],
)
def test_invalid_or_transitioning_sets_never_become_empty(tmp_path, kind):
    current, legacy, payload = source_set(tmp_path)
    if kind == "transition":
        payload["state"] = "preparing"
    elif kind == "duplicate-id":
        payload["current"]["id"] = payload["sealed"][0]["id"]
    elif kind == "duplicate-file":
        payload["sealed"].append(entry(legacy, "different-id"))
    elif kind == "escape":
        payload["sealed"][0]["file"] = "../elsewhere.sqlite"
    elif kind == "wrong-current":
        payload["current"]["file"] = legacy.name
    elif kind == "bad-identity":
        payload["sealed"][0]["identity"]["size"] = True
    current.with_name(current.name + ".segments.lock").touch()
    manifest_path(current).write_text(
        "invalid" if kind == "bad-json" else json.dumps(payload)
    )
    with pytest.raises(SourceUnavailableError):
        resolve_machine_sources(current)


def test_sealed_change_and_uncheckpointed_wal_refuse(tmp_path):
    current, legacy, _ = source_set(tmp_path)
    wal = legacy.with_name(legacy.name + "-wal")
    wal.write_bytes(b"uncheckpointed")
    with pytest.raises(SourceUnavailableError, match="WAL"):
        resolve_machine_sources(current)
    wal.unlink()
    legacy.write_bytes(b"rewritten")
    with pytest.raises(SourceUnavailableError, match="identity changed"):
        resolve_machine_sources(current)


def test_active_append_allowed_but_replacement_refused(tmp_path):
    current, _, _ = source_set(tmp_path)
    with current.open("ab") as stream:
        stream.write(b" append")
    assert resolve_machine_sources(current).segments
    replacement = tmp_path / "replacement"
    replacement.write_bytes(current.read_bytes())
    replacement.replace(current)
    with pytest.raises(SourceUnavailableError, match="identity changed"):
        resolve_machine_sources(current)


def test_manifest_snapshot_freezes_only_control_bytes_and_refuses_stale_current(
    tmp_path,
):
    current, _, payload = source_set(tmp_path)
    with input_snapshot() as snapshot:
        selected = resolve_machine_sources(current)
        assert set(snapshot.files) == {manifest_path(current)}
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"next segment")
        replacement.replace(current)
        payload["current"] = entry(current, "current-b")
        payload["generation"] = "generation-b"
        manifest_path(current).write_text(json.dumps(payload))
        with pytest.raises(SourceUnavailableError, match="identity changed"):
            resolve_machine_sources(current)
        assert selected.generation == "generation-a"


def test_manifest_appearing_during_operation_refuses_mixed_source_set(tmp_path):
    current = tmp_path / "current.sqlite"
    current.write_bytes(b"old")
    with input_snapshot():
        assert resolve_machine_sources(current).manifest is None
        source_set(tmp_path)
        with pytest.raises(SourceUnavailableError, match="appeared"):
            resolve_machine_sources(current)


def test_segment_and_manifest_symlinks_are_unavailable(tmp_path):
    current, legacy, _ = source_set(tmp_path)
    retained = tmp_path / "retained"
    legacy.rename(retained)
    legacy.symlink_to(retained)
    with pytest.raises(SourceUnavailableError):
        resolve_machine_sources(current)
    legacy.unlink()
    retained.rename(legacy)
    manifest = manifest_path(current)
    manifest.unlink()
    manifest.symlink_to("absent")
    with pytest.raises(SourceUnavailableError):
        resolve_machine_sources(current)


def sqlite_sources(tmp_path, monkeypatch, *, old=None, new=None):
    from contextlib import closing
    import sqlite3
    from lynchpin.sources import machine, machine_schema as schema
    from lynchpin.ingest import machine_materialize

    tables = {
        "metric_sample": (*schema.BASE_METRIC_COLUMNS, *schema.OPTIONAL_METRIC_COLUMNS),
        "gpu_sample": schema.EXPECTED_GPU_COLUMNS,
        "network_sample": schema.EXPECTED_NETWORK_COLUMNS,
        "service_state": schema.EXPECTED_SERVICE_STATE_COLUMNS,
        "block_device_sample": schema.EXPECTED_BLOCK_DEVICE_COLUMNS,
        "service_cgroup_io_sample": schema.EXPECTED_SERVICE_CGROUP_IO_COLUMNS,
        "service_cgroup_pressure_sample": schema.EXPECTED_SERVICE_CGROUP_PRESSURE_COLUMNS,
        "process_io_delta_sample": schema.EXPECTED_PROCESS_IO_DELTA_COLUMNS,
        "process_memory_sample": schema.EXPECTED_PROCESS_MEMORY_COLUMNS,
        "cgroup_memory_sample": schema.EXPECTED_CGROUP_MEMORY_COLUMNS,
        "kill_event": schema.EXPECTED_KILL_EVENT_COLUMNS,
    }
    current, legacy = tmp_path / "current.sqlite", tmp_path / "legacy.sqlite"
    old = (
        old
        if old is not None
        else [(1, "2026-01-01T12:00:00+00:00"), (2, "2026-01-02T00:00:00+00:00")]
    )
    # A later captured record can carry an earlier observation timestamp.
    new = (
        new
        if new is not None
        else [(3, "2026-01-01T11:00:00+00:00"), (4, "2026-01-02T01:00:00+00:00")]
    )
    for path, samples in ((legacy, old), (current, new)):
        with closing(sqlite3.connect(path)) as conn:
            for table, names in tables.items():
                columns = tuple(dict.fromkeys(("id", *names)))
                text_columns = {
                    "observed_at",
                    "host",
                    "boot_id",
                    "comm",
                    "exe",
                    "cgroup",
                    "unit",
                    "scope",
                    "command_line",
                    "gap_codes_json",
                    "gpu_pstate",
                    "active_state",
                    "sub_state",
                    "device",
                    "interface",
                    "reason",
                }
                definitions = ",".join(
                    f"{name} {'TEXT' if name in text_columns else 'INTEGER'}"
                    for name in columns
                )
                conn.execute(f"CREATE TABLE {table} ({definitions})")
                conn.execute(f"CREATE INDEX {table}_time ON {table}(observed_at)")
                if table not in {
                    "metric_sample",
                    "gpu_sample",
                    "process_memory_sample",
                    "service_state",
                }:
                    continue
                for row_id, timestamp in samples:
                    values = {name: 0 for name in columns}
                    values.update(
                        id=row_id,
                        observed_at=timestamp,
                        host="fixture-host",
                        boot_id="fixture-boot",
                        schema_version=5,
                        pid=7,
                        process_start_time_ticks=100,
                        comm="fixture-task",
                        unit="fixture.service",
                        scope="system",
                        active_state="failed" if path == current else "active",
                        sub_state="failed" if path == current else "running",
                        gap_codes_json="[]",
                        pss_anon_kb=row_id * 10,
                        pss_kb=row_id * 10,
                        io_psi_some_avg60=row_id,
                        memory_psi_some_avg60=row_id * 2,
                        swap_used_mb=row_id * 3,
                    )
                    conn.execute(
                        f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                        [values[name] for name in columns],
                    )
            conn.execute(
                "CREATE INDEX service_unit_time ON service_state(unit,observed_at)"
            )
            conn.execute(
                "CREATE TABLE source_status(source TEXT PRIMARY KEY, checked_at TEXT, status TEXT, reason TEXT, payload_json TEXT)"
            )
            conn.execute(
                "INSERT INTO source_status VALUES (?,?,?,?,?)",
                (
                    "fixture-source",
                    "2026-01-01T00:00:00Z"
                    if path == current
                    else "2026-01-02T00:00:00Z",
                    "new" if path == current else "old",
                    None,
                    "{}",
                ),
            )
            conn.commit()
    current.with_name(current.name + ".segments.lock").touch()
    manifest_path(current).write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "state": "ready",
                "generation": "fixture-generation",
                "origin": {
                    "path": str(current.resolve()),
                    "device": legacy.stat().st_dev,
                    "inode": legacy.stat().st_ino,
                },
                "sealed": [entry(legacy, "legacy")],
                "current": entry(current, "current"),
            }
        )
    )
    cfg = SimpleNamespace(machine_telemetry_db=current, local_root=tmp_path / "local")
    monkeypatch.setattr(machine, "get_config", lambda: cfg)
    monkeypatch.setattr(machine_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: cfg)
    monkeypatch.setattr("lynchpin.analysis.daytime_correlates.get_config", lambda: cfg)
    monkeypatch.setattr(machine, "default_route_interface", lambda: None)
    return current, legacy


def test_typed_windows_merge_old_new_late_rows_and_preserve_explicit_file_reads(
    tmp_path, monkeypatch
):
    from datetime import date
    from lynchpin.sources import machine

    current, legacy = sqlite_sources(tmp_path, monkeypatch)
    rows = list(machine.gpu_samples())
    assert [row.source_row_id for row in rows] == [3, 1, 2, 4]
    assert [
        row.source_row_id
        for row in machine.gpu_samples(start=date(2026, 1, 1), end=date(2026, 1, 1))
    ] == [3, 1]
    assert [
        row.source_row_id
        for row in machine.gpu_samples(start=date(2026, 1, 2), end=date(2026, 1, 2))
    ] == [2, 4]
    assert [row.source_row_id for row in machine.gpu_samples(path=legacy)] == [1, 2]
    assert [row.source_row_id for row in machine.gpu_samples(path=current)] == [3, 4]
    assert machine.latest_metric_sample().observed_at == rows[-1].observed_at
    assert len(list(machine.process_memory_samples(limit=1))) == 1
    assert next(machine.process_memory_samples(limit=1)).pss_kb == 30


def test_process_lifetime_endpoints_span_single_row_fragments(tmp_path, monkeypatch):
    from lynchpin.sources import machine

    current, legacy = sqlite_sources(
        tmp_path,
        monkeypatch,
        old=[(1, "2026-01-01T23:59:50+00:00")],
        new=[(2, "2026-01-02T00:00:10+00:00")],
    )
    assert not machine.process_memory_growth_candidates(path=legacy)
    assert not machine.process_memory_growth_candidates(path=current)
    rows = machine.process_memory_growth_candidates()
    assert len(rows) == 1 and rows[0].sample_count == 2
    assert rows[0].first_pss_anon_kb == 10 and rows[0].last_pss_anon_kb == 20


def test_segmented_readiness_uses_history_when_current_is_empty(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from lynchpin.sources import machine

    sqlite_sources(tmp_path, monkeypatch, new=[])
    ready = machine.readiness(now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert ready.status == "ready" and ready.live_rows is None
    assert ready.latest_sample_at is not None


def test_partial_merged_iterator_closes_all_actual_connections_without_gc(
    tmp_path, monkeypatch
):
    import sqlite3
    from lynchpin.sources import machine

    sqlite_sources(tmp_path, monkeypatch)
    original, connections = machine.connect_readonly, []

    def connect(path):
        conn = original(path)
        connections.append(conn)
        return conn

    monkeypatch.setattr(machine, "connect_readonly", connect)
    iterator = machine.metric_samples()
    next(iterator)
    iterator.close()
    assert len(connections) == 2
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_segment_replacement_during_connection_open_refuses_and_closes(
    tmp_path, monkeypatch
):
    import shutil
    import sqlite3
    from lynchpin.sources import machine, machine_sqlite

    current, _ = sqlite_sources(tmp_path, monkeypatch)
    original, connections = sqlite3.connect, []

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs)
        connections.append(conn)
        if str(current) in str(args[0]):
            replacement = tmp_path / "replacement"
            shutil.copyfile(current, replacement)
            replacement.replace(current)
        return conn

    monkeypatch.setattr(machine_sqlite.sqlite3, "connect", connect)
    with pytest.raises(SourceUnavailableError):
        list(machine.metric_samples())
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_materialized_historical_days_keep_legacy_rows_and_refuse_missing_members(
    tmp_path, monkeypatch
):
    from datetime import date
    from lynchpin.sources import machine
    from lynchpin.ingest import machine_materialize as materialize

    _, legacy = sqlite_sources(tmp_path, monkeypatch)
    root = tmp_path / "products"

    def output(table):
        return root / f"{table}.ndjson"

    monkeypatch.setattr(machine, "canonical_machine_table_path", output)
    monkeypatch.setattr(materialize, "canonical_machine_table_path", output)
    report = materialize.materialize_machine_telemetry(
        start=date(2026, 1, 1), end=date(2026, 1, 3)
    )
    assert report["tables"]["metric_sample"]["row_count"] == 4
    assert report["tables"]["metric_sample"]["covered_dates"] == [
        "2026-01-01",
        "2026-01-02",
    ]
    before = output("manifest").with_suffix(".json").read_bytes()
    legacy.unlink()
    with pytest.raises(SourceUnavailableError):
        materialize.materialize_machine_telemetry(
            start=date(2026, 1, 1), end=date(2026, 1, 3)
        )
    assert output("manifest").with_suffix(".json").read_bytes() == before


def test_service_evidence_sweeps_across_the_file_boundary(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from lynchpin.graph.capture_service_evidence import capture_service_evidence

    current, _ = sqlite_sources(
        tmp_path,
        monkeypatch,
        old=[(1, "2026-01-01T23:59:50+00:00")],
        new=[(2, "2026-01-02T00:00:10+00:00")],
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema": "sinnix-runtime-inventory-v1",
                "hostname": "fixture-host",
                "classes": {},
                "slices": {},
                "surfaces": {},
                "backups": {},
                "observedServices": [],
                "mounts": [],
                "captures": [
                    {
                        "name": "fixture-capture",
                        "producer": {
                            "surface": "fixture",
                            "unit": "fixture.service",
                            "manager": "system",
                        },
                    }
                ],
            }
        )
    )
    cfg = SimpleNamespace(
        machine_telemetry_db=current, sinnix_runtime_inventory_json=inventory
    )
    start = datetime(2026, 1, 2, tzinfo=timezone.utc)
    report = capture_service_evidence(
        start=start, end=start + timedelta(seconds=20), cfg=cfg
    )
    assert report["status"] == "available"
    assert report["captures"][0]["inactive_seconds"] == 10
    assert report["captures"][0]["unobserved_seconds"] == 0


def test_current_read_connection_excludes_cutover_until_close(tmp_path, monkeypatch):
    import fcntl
    from lynchpin.sources import machine
    from lynchpin.sources.machine_sqlite import machine_lock_path

    current, _ = sqlite_sources(tmp_path, monkeypatch)
    conn = machine.connect_readonly(current)
    with machine_lock_path(current).open("rb") as owner:
        with pytest.raises(BlockingIOError):
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        conn.close()
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SourceUnavailableError, match="cutover"):
            list(machine.metric_samples())
        ready = machine.readiness()
        assert ready.status == "unavailable" and ready.live_rows is None
    assert list(machine.metric_samples())


def test_global_limit_keeps_existing_secondary_order_at_shared_timestamp(
    tmp_path, monkeypatch
):
    from lynchpin.sources import machine

    sqlite_sources(
        tmp_path,
        monkeypatch,
        old=[(1, "2026-01-01T12:00:00Z")],
        new=[(2, "2026-01-01T12:00:00Z")],
    )
    rows = list(machine.process_memory_samples(limit=1))
    assert len(rows) == 1 and rows[0].pss_kb == 20


def test_lossless_rename_retains_original_lineage_without_history_scan(tmp_path):
    from lynchpin.substrate.machine_lineage import sqlite_location_identity

    current, legacy = tmp_path / "current.sqlite", tmp_path / "legacy.sqlite"
    current.write_bytes(b"retained")
    original = current.stat()
    before = sqlite_location_identity(current)
    current.rename(legacy)
    current.write_bytes(b"new current")
    current.with_name(current.name + ".segments.lock").touch()
    manifest_path(current).write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "state": "ready",
                "generation": "after-cutover",
                "origin": {
                    "path": str(current.resolve()),
                    "device": original.st_dev,
                    "inode": original.st_ino,
                },
                "sealed": [entry(legacy, "legacy")],
                "current": entry(current, "current"),
            }
        )
    )
    assert sqlite_location_identity(current) == before


def test_native_package_relocation_keeps_logical_origin_and_relative_members(tmp_path):
    from lynchpin.substrate.machine_lineage import sqlite_location_identity

    root = tmp_path / "package"
    root.mkdir()
    current, _, _ = source_set(root)
    before = sqlite_location_identity(current)
    moved = tmp_path / "relocated-package"
    root.rename(moved)
    selected = resolve_machine_sources(moved / current.name)
    assert selected.lineage_identity() == before
    assert {segment.path.parent for segment in selected.segments} == {moved}


def test_native_duckdb_source_view_merges_and_releases_handles_on_exception(
    tmp_path, monkeypatch
):
    import duckdb
    import fcntl
    from lynchpin.sources.machine_segments import attach_machine_sources
    from lynchpin.sources.machine_sqlite import machine_lock_path

    current, _ = sqlite_sources(tmp_path, monkeypatch)
    sources = resolve_machine_sources(current)
    conn = duckdb.connect(config={"threads": 1, "memory_limit": "128MB"})
    try:
        conn.execute("INSTALL sqlite; LOAD sqlite;")
        with pytest.raises(RuntimeError, match="synthetic"):
            with attach_machine_sources(
                conn,
                sources,
                alias="fixture_src",
                tables=("metric_sample", "source_status"),
            ):
                assert conn.execute(
                    "SELECT id FROM fixture_src.metric_sample ORDER BY observed_at"
                ).fetchall() == [(3,), (1,), (2,), (4,)]
                assert conn.execute(
                    "SELECT status FROM fixture_src.source_status"
                ).fetchall() == [("new",)]
                with machine_lock_path(current).open("rb") as owner:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                raise RuntimeError("synthetic consumer failure")
        assert not conn.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name='fixture_src'"
        ).fetchall()
        assert not any(
            row[0].startswith("fixture_src")
            for row in conn.execute(
                "SELECT database_name FROM duckdb_databases()"
            ).fetchall()
        )
        with machine_lock_path(current).open("rb") as owner:
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        conn.close()


def test_parquet_export_verifies_combined_days_and_reuses_completed_partitions(
    tmp_path, monkeypatch
):
    from datetime import datetime, timezone
    from lynchpin.cli.machine_telemetry_export import run_export

    current, _ = sqlite_sources(tmp_path, monkeypatch)
    lake = tmp_path / "lake"
    now = datetime(2026, 1, 3, tzinfo=timezone.utc)
    result = run_export(
        sqlite_path=current, lake_root=lake, tables=("metric_sample",), now=now
    )
    assert result[0].rows_exported == 4
    files = {
        path: path.stat().st_mtime_ns
        for path in lake.glob("metric_sample/dt=*/*.parquet")
    }
    assert len(files) == 2
    manifest = json.loads((lake / "manifest.json").read_text())
    assert manifest["source_set"]["generation"] == "fixture-generation"
    assert len(manifest["source_set"]["segments"]) == 2
    repeated = run_export(
        sqlite_path=current, lake_root=lake, tables=("metric_sample",), now=now
    )
    assert repeated[0].rows_exported == 0
    assert {path: path.stat().st_mtime_ns for path in files} == files


def test_daily_pressure_combines_segment_denominators(tmp_path, monkeypatch):
    from datetime import datetime
    from lynchpin.analysis.daytime_correlates import _load_machine_daily
    from lynchpin.core.primitives import logical_date

    sqlite_sources(tmp_path, monkeypatch)
    result = _load_machine_daily()
    day = logical_date(datetime.fromisoformat("2026-01-01T12:00:00+00:00"))
    assert result["io_psi_mean"][day] == 2.5
    assert result["mem_psi_mean"][day] == 5
    assert result["swap_max_mb"][day] == 12
    assert result["kill_events"][day] == 0


def test_freshness_sums_historical_window_across_segments(tmp_path, monkeypatch):
    from datetime import date
    import duckdb
    from lynchpin.substrate.machine import load_machine_promotion_freshness

    sqlite_sources(tmp_path, monkeypatch)
    conn = duckdb.connect(config={"threads": 1, "memory_limit": "128MB"})
    try:
        # Keep the test on one real table without constructing unrelated products.
        monkeypatch.setattr(
            "lynchpin.substrate.machine.MACHINE_PROMOTION_FRESHNESS_TABLES",
            (("machine_metric_sample", "metric_sample"),),
        )
        conn.execute("CREATE TABLE machine_metric_sample (observed_at TIMESTAMPTZ)")
        conn.execute(
            "INSERT INTO machine_metric_sample VALUES "
            "('2026-01-01T11:00:00Z'), ('2026-01-01T12:00:00Z'),"
            "('2026-01-02T00:00:00Z'), ('2026-01-02T01:00:00Z')"
        )
        result = load_machine_promotion_freshness(
            conn, window=(date(2026, 1, 1), date(2026, 1, 3))
        )
        assert result[0]["live_window_rows"] == 4
        assert result[0]["substrate_window_rows"] == 4
        assert result[0]["window_row_count_agrees"] is True
        assert result[0]["stale"] is False
    finally:
        conn.close()


def test_native_promotion_preserves_rows_from_both_segments(tmp_path, monkeypatch):
    from datetime import date
    from lynchpin.analysis.active.substrate_promote_machine import _promote_machine_fast
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.source_status import SourceSelection

    current, _ = sqlite_sources(tmp_path, monkeypatch)
    with connect(tmp_path / "facts.duckdb") as conn:
        apply_schema(conn)
        counts = {}
        _promote_machine_fast(
            conn,
            refresh_id="first",
            sqlite_path=current,
            window_start=date(2026, 1, 1),
            window_end=date(2026, 1, 2),
            counts=counts,
            selection=SourceSelection.from_collection({"machine_gpu_sample"}),
        )
        assert counts["machine_gpu_sample"] == 4
        assert conn.execute(
            "SELECT source_row_id FROM machine_gpu_sample ORDER BY source_row_id"
        ).fetchall() == [(1,), (2,), (3,), (4,)]


def test_freshness_missing_segment_table_is_unavailable(tmp_path, monkeypatch):
    from contextlib import closing
    import sqlite3
    import duckdb
    from lynchpin.substrate.machine import load_machine_promotion_freshness

    current, legacy = sqlite_sources(tmp_path, monkeypatch)
    with closing(sqlite3.connect(legacy)) as native:
        native.execute("DROP TABLE metric_sample")
        native.commit()
    payload = json.loads(manifest_path(current).read_text())
    payload["sealed"] = [entry(legacy, "legacy")]
    manifest_path(current).write_text(json.dumps(payload))
    monkeypatch.setattr("lynchpin.substrate.machine.MACHINE_PROMOTION_FRESHNESS_TABLES",
                        (("machine_metric_sample", "metric_sample"),))
    conn = duckdb.connect(config={"threads": 1, "memory_limit": "128MB"})
    try:
        conn.execute("CREATE TABLE machine_metric_sample(observed_at TIMESTAMPTZ)")
        result = load_machine_promotion_freshness(conn)
        assert result[0]["stale"] is None
        assert "required telemetry segment" in result[0]["error"]
    finally:
        conn.close()
