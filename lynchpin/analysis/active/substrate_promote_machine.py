"""Machine telemetry promotion for the materialization DAG substrate step.

Uses DuckDB's SQLite ATTACH to bulk-transfer machine tables directly
(no Python row-by-roundtrip).  Falls back to the iterator path when the
SQLite extension is unavailable or the canonical DB path is absent.
"""

from __future__ import annotations

from lynchpin.sources.machine_segments import MachineSourceSet, resolve_machine_sources, attach_machine_sources, iter_machine_segments
from lynchpin.core.errors import SourceUnavailableError

import gc
from collections.abc import Callable
from dataclasses import replace
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lynchpin.substrate.source_status import (
    SOURCE_MACHINE,
    SOURCE_MACHINE_EXPERIMENTS,
    SOURCE_MACHINE_GPU,
    SOURCE_MACHINE_NETWORK,
    SOURCE_MACHINE_CGROUP_MEMORY,
    SOURCE_MACHINE_KILL_EVENT,
    SOURCE_MACHINE_PROCESS_IO_DELTA,
    SOURCE_MACHINE_PROCESS_MEMORY,
    SOURCE_MACHINE_SERVICE_STATE,
    SourceSelection,
    record_source_status,
)
from lynchpin.substrate._helpers import _staging_table_name

log = logging.getLogger(__name__)


_MACHINE_STAGE_NAMES = {
    SOURCE_MACHINE: "promote_machine_metric_sample",
    SOURCE_MACHINE_SERVICE_STATE: "promote_machine_service_state",
    SOURCE_MACHINE_GPU: "promote_machine_gpu_sample",
    SOURCE_MACHINE_NETWORK: "promote_machine_network_sample",
    SOURCE_MACHINE_PROCESS_IO_DELTA: "promote_machine_process_io_delta_sample",
    SOURCE_MACHINE_PROCESS_MEMORY: "promote_machine_process_memory_sample",
    SOURCE_MACHINE_CGROUP_MEMORY: "promote_machine_cgroup_memory_sample",
    SOURCE_MACHINE_KILL_EVENT: "promote_machine_kill_event",
    SOURCE_MACHINE_EXPERIMENTS: "promote_machine_experiments",
}


def _is_memory_exhaustion(exc: Exception) -> bool:
    import duckdb

    return isinstance(exc, (MemoryError, duckdb.OutOfMemoryException))


def _reject_memory_exhaustion(exc: Exception, *, stage: str) -> None:
    """A memory limit is a stop condition, not a reason for a larger fallback."""
    from lynchpin.substrate.connection import CandidateGenerationRejected

    if _is_memory_exhaustion(exc):
        raise CandidateGenerationRejected(
            f"machine stage {stage} exhausted memory; promotion stopped without fallback"
        ) from exc


def _is_fatal_duckdb_error(exc: Exception) -> bool:
    """Return whether ``exc`` invalidates the connection or its indexes.

    DuckDB 1.1.3 exposes both ``FatalException`` and ``InternalException``.
    The latter is also used for ordinary SQL errors, so the message check is
    deliberately limited to the invalidation and ART/checkpoint signatures
    that require a fresh connection. Retrying one of these on the same
    connection is unsafe.
    """
    import duckdb

    if isinstance(exc, duckdb.FatalException):
        return True
    message = str(exc)
    return any(
        marker in message
        for marker in (
            "database has been invalidated",
            "Corrupted ART index",
            "Failed to delete all rows from index",
            "Invalid node type for TransformToDeprecated",
            "Invalid node type for GetAllocatorIdx",
            "Failed to create checkpoint",
        )
    )


def _machine_stage_failure(
    conn: Any,
    *,
    refresh_id: str,
    stage: str,
    source: str,
    reason: str,
    row_count: int,
    window_start: date,
    window_end: date,
) -> None:
    """Persist one failed stage and its source outcome, best effort.

    The run-step receipt is written first because a source-status write can
    itself fail after DuckDB invalidates a connection. A failure to write the
    second receipt is logged rather than replacing the original stage error.
    """
    from lynchpin.substrate.run_steps import record_run_step

    try:
        record_run_step(
            conn,
            refresh_id=refresh_id,
            step=stage,
            status="error",
            message=reason,
            row_count=row_count,
            finished_at=datetime.now(timezone.utc),
        )
    except Exception:
        log.warning("substrate_promote: could not persist failed stage %s", stage, exc_info=True)
    try:
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=source,
            status="error",
            reason=reason,
            row_count=row_count,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception:
        log.warning("substrate_promote: could not persist failed source %s", source, exc_info=True)


def _raise_fatal_machine_error(exc: Exception, *, stage: str) -> None:
    """Stop after a known-invalidating DuckDB error using connection policy."""
    from lynchpin.substrate.connection import (
        CandidateGenerationRejected,
        in_candidate_generation,
        rebuild_corrupt_substrate,
    )

    if in_candidate_generation():
        raise CandidateGenerationRejected(
            f"machine stage {stage} invalidated the candidate substrate; "
            "the candidate must be discarded and rebuilt from a verified generation"
        ) from exc
    try:
        rebuild_corrupt_substrate()
    except Exception as recovery_exc:
        raise CandidateGenerationRejected(
            f"machine stage {stage} hit a fatal DuckDB error; corrupt substrate "
            f"was quarantined or recovery was rejected: {recovery_exc}"
        ) from exc
    raise CandidateGenerationRejected(
        f"machine stage {stage} hit a fatal DuckDB error; substrate recovery is required"
    ) from exc


def _run_machine_stage(
    conn: Any,
    *,
    refresh_id: str,
    stage: str,
    source: str,
    counts: dict[str, int],
    window_start: date,
    window_end: date,
    fn: Callable[[], None],
) -> bool:
    """Run one machine-table promotion with an append-only durable receipt."""
    from lynchpin.substrate.run_steps import record_run_step

    started_at = datetime.now(timezone.utc)
    before = dict(counts)
    log.info("substrate_promote: starting %s", stage)
    record_run_step(
        conn,
        refresh_id=refresh_id,
        step=stage,
        status="running",
        message="started",
        started_at=started_at,
    )
    try:
        fn()
    except Exception as exc:
        from lynchpin.substrate.connection import CandidateGenerationRejected

        if isinstance(exc, (CandidateGenerationRejected, SourceUnavailableError)):
            raise
        reason = f"{type(exc).__name__}: {exc}"
        row_count = _machine_count_delta(before, counts)
        _machine_stage_failure(
            conn,
            refresh_id=refresh_id,
            stage=stage,
            source=source,
            reason=reason,
            row_count=row_count,
            window_start=window_start,
            window_end=window_end,
        )
        _reject_memory_exhaustion(exc, stage=stage)
        if _is_fatal_duckdb_error(exc):
            _raise_fatal_machine_error(exc, stage=stage)
        log.warning("substrate_promote: %s failed: %s", stage, reason)
        return False
    finally:
        gc.collect()

    record_run_step(
        conn,
        refresh_id=refresh_id,
        step=stage,
        status="success",
        message="finished",
        row_count=_machine_count_delta(before, counts),
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
    )
    return True


def _machine_count_delta(before: dict[str, int], after: dict[str, int]) -> int:
    return sum(max(0, int(value) - int(before.get(key, 0))) for key, value in after.items())


def promote_machine_tables(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
    full_repromote: bool = False,
    recovered_tables: frozenset[str] = frozenset(),
) -> None:
    if not selection.includes(*(
        SOURCE_MACHINE,
        SOURCE_MACHINE_GPU,
        SOURCE_MACHINE_NETWORK,
        SOURCE_MACHINE_CGROUP_MEMORY,
        SOURCE_MACHINE_KILL_EVENT,
        SOURCE_MACHINE_PROCESS_IO_DELTA,
        SOURCE_MACHINE_PROCESS_MEMORY,
        SOURCE_MACHINE_SERVICE_STATE,
        SOURCE_MACHINE_EXPERIMENTS,
    )):
        return

    # ── fast path: DuckDB SQLite ATTACH — every sqlite-backed machine table ──
    # process_io_delta/process_memory/cgroup_memory/kill_event used to be
    # excluded from this path and promoted row-by-row in Python afterwards.
    # They never converged: the row-by-row promoters ran sequentially after the
    # ATTACH tables, and process_io_delta_sample (22 M rows) starved every step
    # behind it, so machine_cgroup_memory_sample held ZERO rows against
    # 4.68 M upstream for the life of the substrate (sinnix-a1dp.1) — not a
    # cgroup-memory bug, just third in a queue that never drained.
    fast_ok = False
    sqlite_path = _machine_sqlite_path()
    sources = resolve_machine_sources(sqlite_path) if sqlite_path is not None else None
    if sources and sqlite_path is not None:
        try:
            _promote_machine_fast(
                conn,
                refresh_id=refresh_id,
                sqlite_path=sqlite_path,
                sources=sources,
                window_start=window_start,
                window_end=window_end,
                counts=counts,
                selection=selection,
                full_repromote=full_repromote,
                recovered_tables=recovered_tables,
            )
            fast_ok = True
        except Exception as exc:
            from lynchpin.substrate.connection import CandidateGenerationRejected

            if isinstance(exc, CandidateGenerationRejected):
                raise
            _reject_memory_exhaustion(exc, stage="promote_machine_fast_setup")
            if _is_fatal_duckdb_error(exc):
                _raise_fatal_machine_error(exc, stage="promote_machine_fast_setup")
            log.warning(
                "substrate_promote: fast machine promotion failed, "
                "falling back to Python iterator path: %s",
                exc,
            )

    # ── slow path: Python row-by-row, fallback only ──
    # Guarded on fast_ok so the sqlite-backed tables are promoted exactly once;
    # running both paths would double-insert every row under one refresh_id.
    if not fast_ok:
        _run_machine_stage(
            conn,
            refresh_id=refresh_id,
            stage="promote_machine_slow_fallback",
            source=SOURCE_MACHINE,
            counts=counts,
            window_start=window_start,
            window_end=window_end,
            fn=lambda: _promote_machine_slow(
                conn, refresh_id, window_start, window_end, counts, selection
            ),
        )

    # Experiments are not a sqlite source (they are files under the machine
    # host root), so they run on both paths as their own durable stage.
    if selection.includes(SOURCE_MACHINE_EXPERIMENTS):
        _run_machine_stage(
            conn,
            refresh_id=refresh_id,
            stage=_MACHINE_STAGE_NAMES[SOURCE_MACHINE_EXPERIMENTS],
            source=SOURCE_MACHINE_EXPERIMENTS,
            counts=counts,
            window_start=window_start,
            window_end=window_end,
            fn=lambda: _promote_experiments(
                conn, refresh_id, window_start, window_end, counts, selection
            ),
        )


# ══════════════════════════════════════════════════════════════════════════════
# Fast path — DuckDB SQLite ATTACH
# ══════════════════════════════════════════════════════════════════════════════


def _machine_sqlite_path() -> Path | None:
    """Return the canonical machine telemetry SQLite path, if configured."""
    from lynchpin.core.config import get_config

    return get_config().machine_telemetry_db


def _machine_projections() -> dict[str, tuple[tuple[str, ...], dict[str, str]]]:
    """Per-source-table (target_columns, override_exprs) for the ATTACH fast path.

    The substrate tables are a *curated transform* of the live SQLite schema,
    not a mirror: unmapped sensor columns are dropped, GPU/cgroup ``id`` is
    retained as ``source_row_id``, and ``*_json`` columns
    are renamed to bare names, ``gap_codes_json`` is parsed to ``VARCHAR[]``,
    ``observed_at`` is cast TEXT -> TIMESTAMPTZ, ``source`` is a provenance
    literal, and ``materialized_at`` is left to the table default. The target
    column list is the canonical ``_*_COLUMNS`` tuple shared with the slow path
    (the single mapping authority); any source column not named here is ignored,
    so future source-schema drift cannot reintroduce a count/type mismatch.

    Each override maps ``target_column -> SQL source expression``; columns absent
    from the override map are selected by their identical source name.
    """
    from lynchpin.substrate.machine import (
        _CGROUP_MEMORY_COLUMNS,
        _GPU_SAMPLE_COLUMNS,
        _KILL_EVENT_COLUMNS,
        _METRIC_SAMPLE_COLUMNS,
        _NETWORK_SAMPLE_COLUMNS,
        _PROCESS_IO_DELTA_COLUMNS,
        _PROCESS_MEMORY_COLUMNS,
        _SERVICE_STATE_COLUMNS,
    )

    ts = "CAST(observed_at AS TIMESTAMPTZ)"
    schema_ver = "CAST(schema_version AS INTEGER)"
    gap = "COALESCE(TRY_CAST(gap_codes_json AS JSON)::VARCHAR[], [])"
    return {
        "metric_sample": (
            _METRIC_SAMPLE_COLUMNS,
            {
                "observed_at": ts,
                "source": "'machine.telemetry'",
                "source_schema_version": schema_ver,
                "gap_codes": gap,
            },
        ),
        "service_state": (
            _SERVICE_STATE_COLUMNS,
            {"observed_at": ts},
        ),
        "gpu_sample": (
            _GPU_SAMPLE_COLUMNS,
            {"observed_at": ts, "source": "'machine.telemetry.gpu'", "source_row_id": "id"},
        ),
        "network_sample": (
            _NETWORK_SAMPLE_COLUMNS,
            {
                "observed_at": ts,
                "source_schema_version": schema_ver,
                "ping": "ping_json",
                "bloat": "bloat_json",
                "iface": "iface_json",
                "nic": "nic_json",
                "tcp": "tcp_json",
                "conntrack": "conntrack_json",
                "pmtu_1492": "CAST(pmtu_1492 AS BOOLEAN)",
                "gap_codes": gap,
            },
        ),
        "process_io_delta_sample": (
            _PROCESS_IO_DELTA_COLUMNS,
            {"observed_at": ts, "source_schema_version": schema_ver},
        ),
        "process_memory_sample": (
            _PROCESS_MEMORY_COLUMNS,
            {"observed_at": ts, "source_schema_version": schema_ver},
        ),
        "cgroup_memory_sample": (
            _CGROUP_MEMORY_COLUMNS,
            {"observed_at": ts, "source_schema_version": schema_ver, "source_row_id": "id"},
        ),
        "kill_event": (
            _KILL_EVENT_COLUMNS,
            {
                "observed_at": ts,
                "source_schema_version": schema_ver,
                "source_row_id": "id",
            },
        ),
    }


def _incremental_reader_and_promoter(
    src_table: str,
) -> tuple[Any, Any, str]:
    """Return (reader, promoter, sample-kwarg-name) for the watermark append path.

    Deliberately routes through the Python ``sqlite3`` readers rather than the
    DuckDB ATTACH scan: a real ``sqlite3`` query against an indexed table gets
    SQLite's own SEARCH plan (sub-second for a one-day tail, per the design
    note's pushdown measurements), where DuckDB's ATTACH scan never pushes the
    predicate down and pays a full-table read no matter how narrow the window.
    Unindexed tables still scan SQLite, but only matching tail rows enter the
    bounded Python batches and the substrate; existing history stays intact.
    """
    from lynchpin.sources.machine import (
        cgroup_memory_samples,
        service_states,
        gpu_samples,
        kill_events,
        metric_samples,
        network_samples,
        process_io_delta_samples,
        process_memory_samples,
    )
    from lynchpin.substrate.machine import (
        promote_machine_cgroup_memory_samples,
        promote_machine_service_states,
        promote_machine_gpu_samples,
        promote_machine_kill_events,
        promote_machine_metric_samples,
        promote_machine_network_samples,
        promote_machine_process_io_delta_samples,
        promote_machine_process_memory_samples,
    )

    table_map: dict[str, tuple[Any, Any, str]] = {
        "service_state": (service_states, promote_machine_service_states, "states"),
        "cgroup_memory_sample": (cgroup_memory_samples, promote_machine_cgroup_memory_samples, "samples"),
        "metric_sample": (metric_samples, promote_machine_metric_samples, "samples"),
        "gpu_sample": (gpu_samples, promote_machine_gpu_samples, "samples"),
        "network_sample": (network_samples, promote_machine_network_samples, "samples"),
        "process_io_delta_sample": (
            process_io_delta_samples, promote_machine_process_io_delta_samples, "samples",
        ),
        "process_memory_sample": (
            process_memory_samples, promote_machine_process_memory_samples, "samples",
        ),
        "kill_event": (kill_events, promote_machine_kill_events, "events"),
    }
    return table_map[src_table]


def _promote_machine_fast(
    conn: Any,
    *,
    refresh_id: str,
    sqlite_path: Path,
    sources: MachineSourceSet | None = None,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
    full_repromote: bool = False,
    recovered_tables: frozenset[str] = frozenset(),
) -> None:
    """Bulk-transfer every sqlite-backed machine table via DuckDB SQLite ATTACH.

    Avoids the Python-object roundtrip penalty, and issues exactly one
    statement per table: DuckDB reads the whole source table for every
    statement (no filter pushdown into SQLite), so statement count is the read
    cost driver and window width is free.

    All tables reuse the requested refresh's watermark or inherit a ready
    predecessor with a covering source window. Only the UTC tail is stored
    under a new generation; typed readers resolve its retained predecessor
    partitions. Explicit full rebuilds and uncovered prefixes use full scans.
    """
    from lynchpin.substrate.machine_lineage import machine_predecessor, machine_relation, record_machine_partition, validate_machine_keys

    sources = sources if sources is not None else resolve_machine_sources(sqlite_path)
    input_fingerprint = sources.lineage_identity()
    t_total = time.monotonic()
    conn.execute("INSTALL SQLITE")
    conn.execute("LOAD SQLITE")

    projections = _machine_projections()

    tables = [
        ("metric_sample", "machine_metric_sample", SOURCE_MACHINE, selection.includes(SOURCE_MACHINE)),
        ("service_state", "machine_service_state", SOURCE_MACHINE_SERVICE_STATE, selection.includes(SOURCE_MACHINE_SERVICE_STATE)),
        ("gpu_sample", "machine_gpu_sample", SOURCE_MACHINE_GPU, selection.includes(SOURCE_MACHINE_GPU)),
        ("network_sample", "machine_network_sample", SOURCE_MACHINE_NETWORK, selection.includes(SOURCE_MACHINE_NETWORK)),
        ("process_io_delta_sample", "machine_process_io_delta_sample", SOURCE_MACHINE_PROCESS_IO_DELTA, selection.includes(SOURCE_MACHINE_PROCESS_IO_DELTA)),
        ("process_memory_sample", "machine_process_memory_sample", SOURCE_MACHINE_PROCESS_MEMORY, selection.includes(SOURCE_MACHINE_PROCESS_MEMORY)),
        ("cgroup_memory_sample", "machine_cgroup_memory_sample", SOURCE_MACHINE_CGROUP_MEMORY, selection.includes(SOURCE_MACHINE_CGROUP_MEMORY)),
        ("kill_event", "machine_kill_event", SOURCE_MACHINE_KILL_EVENT, selection.includes(SOURCE_MACHINE_KILL_EVENT)),
    ]

    with attach_machine_sources(conn, sources, alias="machine_src", tables=(src for src, _, _, enabled in tables if enabled)):
        for src_table, dst_table, source, enabled in tables:
            if not enabled:
                continue
            def promote_table() -> None:
                t0 = time.monotonic()
                # Validate the selected source's presence without scanning unrelated
                # historical tables. A successful zero-row read means this lane is
                # empty; missing/inaccessible tables fail their own stage.
                source_columns = {column[0] for column in conn.execute(f"SELECT * FROM machine_src.{src_table} LIMIT 0").description}
                if src_table in {"gpu_sample", "cgroup_memory_sample"}:
                    if "id" not in source_columns:
                        projections[src_table][1]["source_row_id"] = "NULL"
                watermark = None
                predecessor = None
                if not full_repromote or dst_table in recovered_tables:
                    current_rows = conn.execute(
                        f"SELECT COUNT(*) FROM {dst_table} WHERE refresh_id = ?", [refresh_id]
                    ).fetchone()[0]
                    existing_lineage = conn.execute(
                        "SELECT predecessor_refresh_id, window_start, input_fingerprint FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?",
                        [dst_table, refresh_id],
                    ).fetchone()
                    if not current_rows and existing_lineage is None:
                        predecessor = machine_predecessor(
                            conn, table=dst_table, source=source, refresh_id=refresh_id,
                            window_start=window_start, window_end=window_end,
                            input_fingerprint=input_fingerprint,
                        )
                    relation = machine_relation(conn, dst_table, predecessor or refresh_id)
                    covers_prefix = existing_lineage is None or (
                        existing_lineage[1] is not None and existing_lineage[1] <= window_start
                        and existing_lineage[2] == input_fingerprint
                    )
                    watermark = conn.execute(
                        f"SELECT MAX(observed_at) FROM {relation} "
                        f"WHERE refresh_id = ? "
                        f"AND observed_at >= CAST(? AS TIMESTAMPTZ) "
                        f"AND observed_at < CAST(? AS TIMESTAMPTZ)",
                        [
                            predecessor or refresh_id,
                            _utc_midnight(window_start),
                            _utc_midnight(window_end + timedelta(days=1)),
                        ],
                    ).fetchone()[0] if covers_prefix else None
                if watermark is not None:
                    try:
                        row_count = _promote_machine_table_incremental(
                            conn,
                            src_table=src_table,
                            dst_table=dst_table,
                            refresh_id=refresh_id,
                            sqlite_path=sqlite_path,
                            sources=sources,
                            window_start=window_start,
                            window_end=window_end,
                            watermark=watermark,
                            predecessor=predecessor,
                        )
                    except Exception as exc:
                        if _is_memory_exhaustion(exc):
                            raise
                        if _is_fatal_duckdb_error(exc) or predecessor is not None or existing_lineage is not None:
                            raise
                        log.warning(
                            "substrate_promote: %s incremental promotion failed, "
                            "falling back to full re-promote: %s",
                            dst_table,
                            exc,
                        )
                    else:
                        counts[dst_table] = row_count
                        log.info(
                            "substrate_promote: %s ← %s (incremental, watermark %s): "
                            "%s rows in %.1fs",
                            dst_table,
                            src_table,
                            watermark,
                            f"{row_count:,}",
                            time.monotonic() - t0,
                        )
                        record_source_status(
                            conn,
                            refresh_id=refresh_id,
                            source=source,
                            status="ok" if row_count else "empty",
                            reason=None,
                            row_count=row_count,
                            window_start=window_start,
                            window_end=window_end,
                        )
                        return

                # Validate a complete private replacement before changing the
                # target partition. The outer generation publishes only after
                # every selected source has passed.
                staging_table = _staging_table_name()
                try:
                    columns, overrides = projections[src_table]
                    select_exprs = ", ".join(f"{overrides.get(c, c)} AS {c}" for c in columns)
                    # DuckDB does not push this predicate down into SQLite, so one
                    # statement per table is the measured scan-minimizing route.
                    date_filter, date_params = _source_window_filter(window_start, window_end)
                    conn.execute(
                        f"CREATE TEMP TABLE {staging_table} AS SELECT * FROM {dst_table} WHERE FALSE"
                    )
                    conn.execute(
                        f"INSERT INTO {staging_table} ({', '.join(columns)}, refresh_id) "
                        f"SELECT {select_exprs}, ? AS refresh_id "
                        f"FROM machine_src.{src_table} {date_filter}",
                        [refresh_id, *date_params],
                    )
                    validate_machine_keys(conn, staging_table, refresh_id, table=dst_table)
                    gc.collect()
                    # These writes remain private to the candidate generation;
                    # its atomic publication preserves the earlier serving store.
                    conn.execute(f"DELETE FROM {dst_table} WHERE refresh_id = ?", [refresh_id])
                    conn.execute(
                        f"INSERT INTO {dst_table} ({', '.join(columns)}, refresh_id) "
                        f"SELECT {', '.join(columns)}, refresh_id FROM {staging_table}"
                    )
                    row_count = conn.execute(
                        f"SELECT COUNT(*) FROM {dst_table} WHERE refresh_id = ?",
                        [refresh_id],
                    ).fetchone()[0]
                    record_machine_partition(
                        conn, table=dst_table, refresh_id=refresh_id,
                        window_start=window_start, window_end=window_end,
                        logical_row_count=row_count, input_fingerprint=input_fingerprint,
                    )
                    counts[dst_table] = row_count
                    log.info(
                        "substrate_promote: %s ← machine_src.%s: %s rows in %.1fs",
                        dst_table,
                        src_table,
                        f"{row_count:,}",
                        time.monotonic() - t0,
                    )
                    record_source_status(
                        conn,
                        refresh_id=refresh_id,
                        source=source,
                        status="ok" if row_count else "empty",
                        reason=None,
                        row_count=row_count,
                        window_start=window_start,
                        window_end=window_end,
                    )
                finally:
                    try:
                        conn.execute(f"DROP TABLE IF EXISTS {staging_table}")
                    except Exception:
                        log.debug("could not drop machine promotion staging table", exc_info=True)

            succeeded = _run_machine_stage(
                conn,
                refresh_id=refresh_id,
                stage=_MACHINE_STAGE_NAMES[source],
                source=source,
                counts=counts,
                window_start=window_start,
                window_end=window_end,
                fn=promote_table,
            )
            if not succeeded:
                from lynchpin.substrate.connection import CandidateGenerationRejected

                raise CandidateGenerationRejected(f"machine stage {_MACHINE_STAGE_NAMES[source]} failed; stop before further scans")

        total_elapsed = time.monotonic() - t_total
        log.info("substrate_promote: machine tables done in %.1fs", total_elapsed)


def _utc_midnight(day: date) -> str:
    """Collector partitions and source date windows use UTC calendar days."""
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat()


def _promote_machine_table_incremental(
    conn: Any,
    *,
    src_table: str,
    dst_table: str,
    refresh_id: str,
    sqlite_path: Path,
    sources: MachineSourceSet | None = None,
    window_start: date,
    window_end: date,
    watermark: datetime,
    predecessor: str | None = None,
) -> int:
    """Append only the rows newer than ``watermark``, via the bounded Python reader.

    Re-reads the watermark's own day in full rather
    than trying to resume mid-day, which sidesteps sub-day dedup entirely: the
    tail is deleted before the re-read is inserted, so there is no window in
    which the same row could be both kept and re-inserted under the same
    primary key (``observed_at, ..., refresh_id``).

    Also prunes rows that fell out the trailing edge of the rolling window
    (``< window_start``), since the caller's window advances by roughly a day
    on every run and the full path would otherwise have dropped them.
    """
    sources = sources if sources is not None else resolve_machine_sources(sqlite_path)
    reader, promoter, sample_kwarg = _incremental_reader_and_promoter(src_table)
    tail_start = max(watermark.astimezone(timezone.utc).date(), window_start)

    conn.execute(
        f"DELETE FROM {dst_table} WHERE refresh_id = ? "
        f"AND observed_at >= CAST(? AS TIMESTAMPTZ)",
        [refresh_id, _utc_midnight(tail_start)],
    )
    conn.execute(
        f"DELETE FROM {dst_table} WHERE refresh_id = ? "
        f"AND observed_at < CAST(? AS TIMESTAMPTZ)",
        [refresh_id, _utc_midnight(window_start)],
    )
    kwargs = {sample_kwarg: iter_machine_segments(sources, reader, start=tail_start, end=window_end)}
    promoter(conn, refresh_id=refresh_id, delete_existing=False, **kwargs)
    from lynchpin.substrate.machine_lineage import MACHINE_TABLES, machine_relation, record_machine_partition

    relation = dst_table
    if dst_table in MACHINE_TABLES:
        existing = conn.execute(
            "SELECT predecessor_refresh_id, replacement_start FROM substrate_product_lineage "
            "WHERE product = ? AND refresh_id = ?", [dst_table, refresh_id],
        ).fetchone()
        if predecessor is None and existing is not None:
            predecessor = existing[0]
            if existing[1] is not None:
                tail_start = min(tail_start, existing[1])
        record_machine_partition(
            conn, table=dst_table, refresh_id=refresh_id,
            window_start=window_start, window_end=window_end,
            predecessor=predecessor, replacement_start=tail_start if predecessor else None,
            input_fingerprint=sources.lineage_identity(),
        )
        relation = machine_relation(conn, dst_table, refresh_id)
    count = int(conn.execute(
        f"SELECT COUNT(*) FROM {relation} WHERE refresh_id = ?", [refresh_id],
    ).fetchone()[0])
    if dst_table in MACHINE_TABLES:
        conn.execute(
            "UPDATE substrate_product_lineage SET logical_row_count = ? WHERE product = ? AND refresh_id = ?",
            [count, dst_table, refresh_id],
        )
    return count


def _source_window_filter(window_start: date, window_end: date) -> tuple[str, list[str]]:
    """Return the fast source-window predicate for machine SQLite tables.

    Source ``observed_at`` values are TEXT ISO8601-with-offset and all UTC.
    A half-open text range preserves the inclusive day window without casting
    every source row to DATE. On the live service_state table this is ~20x
    faster than ``CAST(observed_at AS DATE) BETWEEN ...``.
    """
    return (
        "WHERE observed_at >= ? AND observed_at < ?",
        [window_start.isoformat(), (window_end + timedelta(days=1)).isoformat()],
    )

# ══════════════════════════════════════════════════════════════════════════════
# Slow path — Python row-by-row (fallback)
# ══════════════════════════════════════════════════════════════════════════════


def _promote_machine_slow(
    conn: Any,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    """Promote each SQLite-backed table through its own durable stage.

    This definition intentionally follows the legacy private helpers above so
    callers that imported those names during the incremental rollout remain
    import-compatible. The fallback itself uses one stage per table and never
    retries a failed table on the same connection.
    """
    from lynchpin.sources.machine import (
        cgroup_memory_samples,
        gpu_samples,
        kill_events,
        metric_samples,
        network_samples,
        process_io_delta_samples,
        process_memory_samples,
        readiness as machine_readiness,
        service_states,
    )
    from lynchpin.substrate.machine import (
        promote_machine_cgroup_memory_samples,
        promote_machine_gpu_samples,
        promote_machine_kill_events,
        promote_machine_metric_samples,
        promote_machine_network_samples,
        promote_machine_process_io_delta_samples,
        promote_machine_process_memory_samples,
        promote_machine_service_states,
    )

    stages = (
        (SOURCE_MACHINE, "machine_metric_samples", metric_samples, promote_machine_metric_samples, "samples"),
        (SOURCE_MACHINE_SERVICE_STATE, "machine_service_states", service_states, promote_machine_service_states, "states"),
        (SOURCE_MACHINE_GPU, "machine_gpu_samples", gpu_samples, promote_machine_gpu_samples, "samples"),
        (SOURCE_MACHINE_NETWORK, "machine_network_samples", network_samples, promote_machine_network_samples, "samples"),
        (SOURCE_MACHINE_PROCESS_IO_DELTA, "machine_process_io_delta_samples", process_io_delta_samples, promote_machine_process_io_delta_samples, "samples"),
        (SOURCE_MACHINE_PROCESS_MEMORY, "machine_process_memory_samples", process_memory_samples, promote_machine_process_memory_samples, "samples"),
        (SOURCE_MACHINE_CGROUP_MEMORY, "machine_cgroup_memory_samples", cgroup_memory_samples, promote_machine_cgroup_memory_samples, "samples"),
        (SOURCE_MACHINE_KILL_EVENT, "machine_kill_events", kill_events, promote_machine_kill_events, "events"),
    )
    for source, count_key, reader, promoter, kwarg in stages:
        if not selection.includes(source):
            continue

        def promote_table(
            *,
            source: str = source,
            count_key: str = count_key,
            reader: Any = reader,
            promoter: Any = promoter,
            kwarg: str = kwarg,
        ) -> None:
            machine_ready = machine_readiness()
            rows = reader(start=window_start, end=window_end)
            row_count = promoter(
                conn,
                refresh_id=refresh_id,
                **{kwarg: rows},
            )
            counts[count_key] = row_count
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=source,
                status="ok" if row_count else ("unavailable" if machine_ready.status not in {"ready", "empty"} else "empty"),
                reason=machine_ready.reason if not row_count else None,
                row_count=row_count,
                window_start=window_start,
                window_end=window_end,
            )

        _run_machine_stage(
            conn,
            refresh_id=refresh_id,
            stage=_MACHINE_STAGE_NAMES[source],
            source=source,
            counts=counts,
            window_start=window_start,
            window_end=window_end,
            fn=promote_table,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Machine experiments (small volume — Python path is fine)
# ══════════════════════════════════════════════════════════════════════════════


def _promote_experiments(
    conn: Any,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_MACHINE_EXPERIMENTS):
        return

    from lynchpin.sources.machine_experiments import experiment_root, experiment_runs
    from lynchpin.substrate.machine import promote_machine_experiment_runs

    exp_root = experiment_root()
    runs = _validated_experiment_runs(
        experiment_runs(start=window_start, end=window_end)
    )
    run_count = promote_machine_experiment_runs(conn, refresh_id=refresh_id, runs=runs)
    counts["machine_experiment_runs"] = run_count
    exp_reason: str | None
    if run_count:
        status, exp_reason = "ok", None
    elif exp_root.exists():
        status, exp_reason = "empty", "no machine experiment manifests in window"
    else:
        status, exp_reason = "unavailable", f"machine experiment root not found at {exp_root}"
    record_source_status(
        conn, refresh_id=refresh_id, source=SOURCE_MACHINE_EXPERIMENTS,
        status=status, reason=exp_reason, row_count=run_count,
        window_start=window_start, window_end=window_end,
    )


def _validated_experiment_runs(runs: Any) -> list[Any]:
    from lynchpin.analysis.machine.controlled_benchmarks import (
        validate_executed_benchmark_manifest,
    )

    validated = []
    for run in runs:
        try:
            payload = json.loads(run.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            validated.append(
                replace(
                    run,
                    validation_status="invalid",
                    validation_issues=(f"cannot re-read manifest for validation: {exc}",),
                    validation_warnings=(),
                    manifest_validation={
                        "valid": False,
                        "issues": [f"cannot re-read manifest for validation: {exc}"],
                        "warnings": [],
                    },
                )
            )
            continue
        if not isinstance(payload, dict):
            validated.append(
                replace(
                    run,
                    validation_status="invalid",
                    validation_issues=("manifest root must be an object",),
                    validation_warnings=(),
                    manifest_validation={
                        "valid": False,
                        "issues": ["manifest root must be an object"],
                        "warnings": [],
                    },
                )
            )
            continue
        validation = validate_executed_benchmark_manifest(
            payload,
            manifest_path=run.manifest_path,
            require_file_refs=False,
        )
        validated.append(
            replace(
                run,
                validation_status="valid" if validation.valid else "invalid",
                validation_issues=tuple(validation.issues),
                validation_warnings=tuple(validation.warnings),
                manifest_validation=validation.to_dict(),
            )
        )
    return validated
