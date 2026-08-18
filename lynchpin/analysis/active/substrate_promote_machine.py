"""Machine telemetry promotion for the materialization DAG substrate step.

Uses DuckDB's SQLite ATTACH to bulk-transfer machine tables directly
(no Python row-by-roundtrip).  Falls back to the iterator path when the
SQLite extension is unavailable or the canonical DB path is absent.
"""

from __future__ import annotations

import gc
from dataclasses import replace
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .substrate_promote_status import (
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
from lynchpin.substrate._helpers import _staging_refresh_id

log = logging.getLogger(__name__)


def promote_machine_tables(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
    full_repromote: bool = False,
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
    if sqlite_path and sqlite_path.exists():
        try:
            _promote_machine_fast(
                conn,
                refresh_id=refresh_id,
                sqlite_path=sqlite_path,
                window_start=window_start,
                window_end=window_end,
                counts=counts,
                selection=selection,
                full_repromote=full_repromote,
            )
            fast_ok = True
        except Exception as exc:
            log.warning(
                "substrate_promote: fast machine promotion failed, "
                "falling back to Python iterator path: %s",
                exc,
            )

    # ── slow path: Python row-by-row, fallback only ──
    # Guarded on fast_ok so the sqlite-backed tables are promoted exactly once;
    # running both paths would double-insert every row under one refresh_id.
    steps: tuple[Any, ...] = ()
    if not fast_ok:
        _promote_machine_slow(conn, refresh_id, window_start, window_end, counts, selection)
        steps = (
            _promote_machine_process_io_slow,
            _promote_machine_process_memory_slow,
            _promote_machine_cgroup_memory_slow,
            _promote_machine_kill_event_slow,
        )
    # Experiments are not a sqlite source (they are files under the machine
    # host root), so they run on both paths.
    steps += (_promote_experiments,)

    # Each per-table promoter already records its own success/error status
    # internally; call them as INDEPENDENT steps (not chained inside one shared
    # try/except) so a bug in one — including one whose own exception handler
    # itself raises, e.g. because a prior DuckDB internal error left the
    # connection aborted — cannot silently skip the promotion attempt (and
    # status write) for every source called after it. This was an observed real
    # failure shape (sinnix-kx4): machine_experiments' substrate_source_status
    # row went stale in lockstep with machine_cgroup_memory_sample's, both stuck
    # on the same date, while sibling sources kept refreshing daily.
    for step in steps:
        try:
            step(conn, refresh_id, window_start, window_end, counts, selection)
        except Exception as exc:
            log.warning("substrate_promote: %s failed: %s", step.__name__, exc)


# ══════════════════════════════════════════════════════════════════════════════
# Fast path — DuckDB SQLite ATTACH
# ══════════════════════════════════════════════════════════════════════════════


def _machine_sqlite_path() -> Path | None:
    """Return the canonical machine telemetry SQLite path, if configured."""
    import os

    from lynchpin.core.config import get_config

    cfg = get_config()
    machine_root = cfg.machine_host_root
    if machine_root is None:
        return None
    db_path = Path(os.environ.get("LYNCHPIN_MACHINE_TELEMETRY_DB", str(Path(machine_root) / "telemetry.sqlite")))
    return db_path


def _machine_projections() -> dict[str, tuple[tuple[str, ...], dict[str, str]]]:
    """Per-source-table (target_columns, override_exprs) for the ATTACH fast path.

    The substrate tables are a *curated transform* of the live SQLite schema,
    not a mirror: ``id`` and extra sensor columns are dropped, ``*_json`` columns
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
            {"observed_at": ts, "source": "'machine.telemetry.gpu'"},
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
            {"observed_at": ts, "source_schema_version": schema_ver},
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


# Tables with no usable ``observed_at`` SQLite index (confirmed via EXPLAIN
# QUERY PLAN, sinnix-2g54 design note §3): any predicate-filtered read costs a
# full table scan regardless of window width, so there is nothing for the
# incremental watermark to save here and these always take the one-scan
# ATTACH path. Adding the missing index is deliberately rejected (permanent
# write amplification for a benefit ``_promote_machine_fast``'s single scan
# already captures).
_UNINDEXED_MACHINE_TABLES = frozenset({"service_state", "cgroup_memory_sample"})


def _incremental_reader_and_promoter(
    src_table: str,
) -> tuple[Any, Any, str]:
    """Return (reader, promoter, sample-kwarg-name) for the watermark append path.

    Deliberately routes through the Python ``sqlite3`` readers rather than the
    DuckDB ATTACH scan: a real ``sqlite3`` query against an indexed table gets
    SQLite's own SEARCH plan (sub-second for a one-day tail, per the design
    note's pushdown measurements), where DuckDB's ATTACH scan never pushes the
    predicate down and pays a full-table read no matter how narrow the window.
    """
    from lynchpin.sources.machine import (
        gpu_samples,
        kill_events,
        metric_samples,
        network_samples,
        process_io_delta_samples,
        process_memory_samples,
    )
    from lynchpin.substrate.machine import (
        promote_machine_gpu_samples,
        promote_machine_kill_events,
        promote_machine_metric_samples,
        promote_machine_network_samples,
        promote_machine_process_io_delta_samples,
        promote_machine_process_memory_samples,
    )

    table_map: dict[str, tuple[Any, Any, str]] = {
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
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
    full_repromote: bool = False,
) -> None:
    """Bulk-transfer every sqlite-backed machine table via DuckDB SQLite ATTACH.

    Avoids the Python-object roundtrip penalty, and issues exactly one
    statement per table: DuckDB reads the whole source table for every
    statement (no filter pushdown into SQLite), so statement count is the read
    cost driver and window width is free.

    Indexed tables (everything but ``_UNINDEXED_MACHINE_TABLES``) additionally
    check for an existing watermark under this ``refresh_id`` and, when one is
    found (a prior run already populated this refresh_id/window) and
    ``full_repromote`` is not set, take the incremental append path instead —
    see ``_promote_machine_table_incremental``. ``refresh_id`` is a
    deterministic function of the requested window (see
    ``materialize._machine_analysis_refresh_id``), so the daily rolling-window
    call reuses the same refresh_id every day and this is the steady-state
    path in production; a genuinely new window (first backfill, an ad hoc
    ``--start``/``--end``) has no watermark yet and takes the full path once.
    """
    t_total = time.monotonic()
    conn.execute("INSTALL SQLITE")
    conn.execute("LOAD SQLITE")
    conn.execute(f"ATTACH '{sqlite_path}' AS machine_src (TYPE SQLITE)")

    from lynchpin.sources.machine import readiness as machine_readiness

    machine_ready = machine_readiness()
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

    for src_table, dst_table, source, enabled in tables:
        if not enabled:
            continue
        t0 = time.monotonic()
        watermark = None
        if not full_repromote and src_table not in _UNINDEXED_MACHINE_TABLES:
            watermark = conn.execute(
                f"SELECT MAX(observed_at) FROM {dst_table} "
                f"WHERE refresh_id = ? "
                f"AND observed_at >= CAST(? AS TIMESTAMPTZ) "
                f"AND observed_at < CAST(? AS TIMESTAMPTZ)",
                [
                    refresh_id,
                    window_start.isoformat(),
                    (window_end + timedelta(days=1)).isoformat(),
                ],
            ).fetchone()[0]
        if watermark is not None:
            try:
                row_count = _promote_machine_table_incremental(
                    conn,
                    src_table=src_table,
                    dst_table=dst_table,
                    refresh_id=refresh_id,
                    sqlite_path=sqlite_path,
                    window_start=window_start,
                    window_end=window_end,
                    watermark=watermark,
                )
                counts[dst_table] = row_count
                elapsed = time.monotonic() - t0
                log.info(
                    "substrate_promote: %s ← %s (incremental, watermark %s): "
                    "%s rows in %.1fs",
                    dst_table, src_table, watermark, f"{row_count:,}", elapsed,
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=source,
                    status="ok" if row_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                    reason=machine_ready.reason if not row_count else None,
                    row_count=row_count,
                    window_start=window_start,
                    window_end=window_end,
                )
                continue
            except Exception as exc:
                log.warning(
                    "substrate_promote: %s incremental promotion failed, "
                    "falling back to full re-promote: %s",
                    dst_table, exc,
                )
        # Stage into a private refresh_id first; only swap it onto the real
        # refresh_id once every day-chunk has landed. This mirrors
        # lynchpin.substrate._helpers.promote_rows's interruption-safety
        # design: a DELETE-then-INSERT of the SAME refresh_id (the previous
        # design here) commits the DELETE immediately in DuckDB autocommit, so
        # a process death (e.g. an OOM kill) or a mid-loop exception partway
        # through the day-chunk INSERTs left the target refresh_id with STALE
        # or ZERO rows and no error ever recorded — the exact "silent
        # promotion stall" observed on machine_cgroup_memory_sample /
        # machine_service_state (sinnix-kx4). Staging first means the target
        # refresh_id's existing rows are untouched until the new data is
        # fully written.
        write_id = _staging_refresh_id(refresh_id)
        try:
            columns, overrides = projections[src_table]
            select_exprs = ", ".join(f"{overrides.get(c, c)} AS {c}" for c in columns)
            # ONE statement for the whole window. DuckDB does not push this
            # predicate down into SQLite -- EXPLAIN shows SQLITE_SCAN under a
            # FILTER -- so every statement issued here reads the ENTIRE source
            # table regardless of the WHERE and regardless of any observed_at
            # index. Chunking therefore multiplies a full table scan by the
            # chunk count and buys nothing: measured against the live 28 GB
            # store, splitting a 90-day window into 4 monthly chunks took
            # metric_sample from 23.6s to 65.5s for identical output.
            #
            # Chunking was originally per-day, and it is tempting to read it as
            # a memory bound. It is not one. Commit memory here is dominated by
            # the target table's ART indexes (machine_service_state carries a
            # 5-column PK plus three secondary indexes), which are memory-
            # resident, cannot spill, and accumulate across statements written
            # under one refresh_id. Chunking a 16.6 M-row promote into 4 pieces
            # hit the SAME "failed to pin block (5.5 GiB/5.5 GiB used)" commit
            # failure as one statement did -- it just took 18 minutes to get
            # there instead of 3. Bounding that cost is an index-design
            # question, not a chunk-size one. Once the watermark path above is
            # warm, this full path only runs for the first backfill of a given
            # refresh_id/window or an explicit --full-repromote, which is where
            # that memory cost belongs (a one-time or operator-invoked cost,
            # not a steady-state one).
            date_filter, date_params = _source_window_filter(window_start, window_end)
            conn.execute(
                f"INSERT INTO {dst_table} ({', '.join(columns)}, refresh_id) "
                f"SELECT {select_exprs}, ? AS refresh_id "
                f"FROM machine_src.{src_table} {date_filter}",
                [write_id, *date_params],
            )
            gc.collect()
            # Full window staged successfully — swap onto the real
            # refresh_id as two fast, separate autocommit statements (NOT one
            # transaction: DuckDB's PK index does not see an in-transaction
            # DELETE, so DELETE+INSERT of the same key in one transaction
            # trips a phantom duplicate-key error).
            conn.execute(f"DELETE FROM {dst_table} WHERE refresh_id = ?", [refresh_id])
            conn.execute(
                f"UPDATE {dst_table} SET refresh_id = ? WHERE refresh_id = ?",
                [refresh_id, write_id],
            )
            row_count = conn.execute(
                f"SELECT COUNT(*) FROM {dst_table} WHERE refresh_id = ?",
                [refresh_id],
            ).fetchone()[0]
            counts[dst_table] = row_count
            elapsed = time.monotonic() - t0
            log.info(
                "substrate_promote: %s ← machine_src.%s: %s rows in %.1fs",
                dst_table, src_table, f"{row_count:,}", elapsed,
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=source,
                status="ok" if row_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason if not row_count else None,
                row_count=row_count,
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as exc:
            log.warning("substrate_promote: %s promotion failed: %s", dst_table, exc)
            try:
                # Best-effort: drop any partially-staged rows from this
                # attempt. The target refresh_id's prior data was never
                # touched, so this is pure cleanup, not recovery.
                conn.execute(f"DELETE FROM {dst_table} WHERE refresh_id = ?", [write_id])
            except Exception:
                pass
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=source,
                status="error",
                reason=str(exc),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )

    total_elapsed = time.monotonic() - t_total
    log.info("substrate_promote: machine tables done in %.1fs", total_elapsed)


def _promote_machine_table_incremental(
    conn: Any,
    *,
    src_table: str,
    dst_table: str,
    refresh_id: str,
    sqlite_path: Path,
    window_start: date,
    window_end: date,
    watermark: datetime,
) -> int:
    """Append only the rows newer than ``watermark``, via the indexed Python reader.

    Re-reads the watermark's own day in full (cheap — one indexed day) rather
    than trying to resume mid-day, which sidesteps sub-day dedup entirely: the
    tail is deleted before the re-read is inserted, so there is no window in
    which the same row could be both kept and re-inserted under the same
    primary key (``observed_at, ..., refresh_id``).

    Also prunes rows that fell out the trailing edge of the rolling window
    (``< window_start``), since the caller's window advances by roughly a day
    on every run and the full path would otherwise have dropped them.
    """
    reader, promoter, sample_kwarg = _incremental_reader_and_promoter(src_table)
    tail_start = max(watermark.date(), window_start)

    conn.execute(
        f"DELETE FROM {dst_table} WHERE refresh_id = ? "
        f"AND observed_at >= CAST(? AS TIMESTAMPTZ)",
        [refresh_id, tail_start.isoformat()],
    )
    conn.execute(
        f"DELETE FROM {dst_table} WHERE refresh_id = ? "
        f"AND observed_at < CAST(? AS TIMESTAMPTZ)",
        [refresh_id, window_start.isoformat()],
    )
    kwargs = {sample_kwarg: reader(start=tail_start, end=window_end, path=sqlite_path)}
    promoter(conn, refresh_id=refresh_id, delete_existing=False, **kwargs)
    return conn.execute(
        f"SELECT COUNT(*) FROM {dst_table} WHERE refresh_id = ?",
        [refresh_id],
    ).fetchone()[0]


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
    from lynchpin.substrate.machine import (
        promote_machine_gpu_samples,
        promote_machine_metric_samples,
        promote_machine_network_samples,
        promote_machine_cgroup_memory_samples,
        promote_machine_process_io_delta_samples,
        promote_machine_process_memory_samples,
        promote_machine_service_states,
    )

    if not selection.includes(
        SOURCE_MACHINE,
        SOURCE_MACHINE_SERVICE_STATE,
        SOURCE_MACHINE_GPU,
        SOURCE_MACHINE_NETWORK,
        SOURCE_MACHINE_PROCESS_IO_DELTA,
        SOURCE_MACHINE_PROCESS_MEMORY,
        SOURCE_MACHINE_CGROUP_MEMORY,
    ):
        return

    try:
        from lynchpin.sources.machine import (
            gpu_samples,
            metric_samples,
            network_samples,
            process_io_delta_samples,
            process_memory_samples,
            cgroup_memory_samples,
            readiness as machine_readiness,
            service_states,
        )

        machine_ready = machine_readiness()
        if selection.includes(SOURCE_MACHINE):
            live_count = promote_machine_metric_samples(
                conn,
                refresh_id=refresh_id,
                samples=metric_samples(start=window_start, end=window_end),
            )
            counts["machine_metric_samples"] = live_count
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE,
                status="ok" if live_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason, row_count=live_count,
                window_start=window_start, window_end=window_end,
            )
        if selection.includes(SOURCE_MACHINE_SERVICE_STATE):
            service_count = promote_machine_service_states(
                conn, refresh_id=refresh_id,
                states=service_states(start=window_start, end=window_end),
            )
            counts["machine_service_states"] = service_count
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_SERVICE_STATE,
                status="ok" if service_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason, row_count=service_count,
                window_start=window_start, window_end=window_end,
            )
        if selection.includes(SOURCE_MACHINE_GPU):
            gpu_count = promote_machine_gpu_samples(
                conn, refresh_id=refresh_id,
                samples=gpu_samples(start=window_start, end=window_end),
            )
            counts["machine_gpu_samples"] = gpu_count
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_GPU,
                status="ok" if gpu_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason, row_count=gpu_count,
                window_start=window_start, window_end=window_end,
            )
        if selection.includes(SOURCE_MACHINE_NETWORK):
            network_count = promote_machine_network_samples(
                conn, refresh_id=refresh_id,
                samples=network_samples(start=window_start, end=window_end),
            )
            counts["machine_network_samples"] = network_count
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_NETWORK,
                status="ok" if network_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason, row_count=network_count,
                window_start=window_start, window_end=window_end,
            )
        if selection.includes(SOURCE_MACHINE_PROCESS_IO_DELTA):
            process_io_count = promote_machine_process_io_delta_samples(
                conn, refresh_id=refresh_id,
                samples=process_io_delta_samples(start=window_start, end=window_end),
            )
            counts["machine_process_io_delta_samples"] = process_io_count
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_PROCESS_IO_DELTA,
                status="ok" if process_io_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason, row_count=process_io_count,
                window_start=window_start, window_end=window_end,
            )
        if selection.includes(SOURCE_MACHINE_PROCESS_MEMORY):
            process_memory_count = promote_machine_process_memory_samples(
                conn, refresh_id=refresh_id,
                samples=process_memory_samples(start=window_start, end=window_end),
            )
            counts["machine_process_memory_samples"] = process_memory_count
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_PROCESS_MEMORY,
                status="ok" if process_memory_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason, row_count=process_memory_count,
                window_start=window_start, window_end=window_end,
            )
        if selection.includes(SOURCE_MACHINE_CGROUP_MEMORY):
            cgroup_memory_count = promote_machine_cgroup_memory_samples(
                conn, refresh_id=refresh_id,
                samples=cgroup_memory_samples(start=window_start, end=window_end),
            )
            counts["machine_cgroup_memory_samples"] = cgroup_memory_count
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_MACHINE_CGROUP_MEMORY,
                status="ok" if cgroup_memory_count else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
                reason=machine_ready.reason, row_count=cgroup_memory_count,
                window_start=window_start, window_end=window_end,
            )
    except Exception as exc:
        log.warning("substrate_promote: machine telemetry promotion skipped: %s", exc)
        for source in (SOURCE_MACHINE, SOURCE_MACHINE_SERVICE_STATE, SOURCE_MACHINE_GPU, SOURCE_MACHINE_NETWORK, SOURCE_MACHINE_PROCESS_IO_DELTA, SOURCE_MACHINE_PROCESS_MEMORY, SOURCE_MACHINE_CGROUP_MEMORY):
            if selection.includes(source):
                record_source_status(
                    conn, refresh_id=refresh_id, source=source,
                    status="error", reason=str(exc), row_count=0,
                    window_start=window_start, window_end=window_end,
                )


def _promote_machine_process_io_slow(
    conn: Any,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_MACHINE_PROCESS_IO_DELTA):
        return
    try:
        from lynchpin.sources.machine import (
            process_io_delta_samples,
            readiness as machine_readiness,
        )
        from lynchpin.substrate.machine import promote_machine_process_io_delta_samples

        machine_ready = machine_readiness()
        process_io_count = promote_machine_process_io_delta_samples(
            conn,
            refresh_id=refresh_id,
            samples=process_io_delta_samples(start=window_start, end=window_end),
        )
        counts["machine_process_io_delta_samples"] = process_io_count
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_PROCESS_IO_DELTA,
            status="ok"
            if process_io_count
            else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
            reason=machine_ready.reason,
            row_count=process_io_count,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: process I/O delta promotion skipped: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_PROCESS_IO_DELTA,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )


def _promote_machine_process_memory_slow(
    conn: Any,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_MACHINE_PROCESS_MEMORY):
        return
    try:
        from lynchpin.sources.machine import (
            process_memory_samples,
            readiness as machine_readiness,
        )
        from lynchpin.substrate.machine import promote_machine_process_memory_samples

        machine_ready = machine_readiness()
        process_memory_count = promote_machine_process_memory_samples(
            conn,
            refresh_id=refresh_id,
            samples=process_memory_samples(start=window_start, end=window_end),
        )
        counts["machine_process_memory_samples"] = process_memory_count
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_PROCESS_MEMORY,
            status="ok"
            if process_memory_count
            else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
            reason=machine_ready.reason,
            row_count=process_memory_count,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: process memory promotion skipped: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_PROCESS_MEMORY,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )


def _promote_machine_cgroup_memory_slow(
    conn: Any,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_MACHINE_CGROUP_MEMORY):
        return
    try:
        from lynchpin.sources.machine import (
            cgroup_memory_samples,
            readiness as machine_readiness,
        )
        from lynchpin.substrate.machine import promote_machine_cgroup_memory_samples

        machine_ready = machine_readiness()
        cgroup_memory_count = promote_machine_cgroup_memory_samples(
            conn,
            refresh_id=refresh_id,
            samples=cgroup_memory_samples(start=window_start, end=window_end),
        )
        counts["machine_cgroup_memory_samples"] = cgroup_memory_count
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_CGROUP_MEMORY,
            status="ok"
            if cgroup_memory_count
            else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
            reason=machine_ready.reason,
            row_count=cgroup_memory_count,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: cgroup memory promotion skipped: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_CGROUP_MEMORY,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )


def _promote_machine_kill_event_slow(
    conn: Any,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_MACHINE_KILL_EVENT):
        return
    try:
        from lynchpin.sources.machine import (
            kill_events,
            readiness as machine_readiness,
        )
        from lynchpin.substrate.machine import promote_machine_kill_events

        machine_ready = machine_readiness()
        kill_event_count = promote_machine_kill_events(
            conn,
            refresh_id=refresh_id,
            events=kill_events(start=window_start, end=window_end),
        )
        counts["machine_kill_events"] = kill_event_count
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_KILL_EVENT,
            status="ok"
            if kill_event_count
            else ("unavailable" if machine_ready.status == "unavailable" else "empty"),
            reason=machine_ready.reason,
            row_count=kill_event_count,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: kill event promotion skipped: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_KILL_EVENT,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
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

    try:
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
    except Exception as exc:
        log.warning("substrate_promote: machine experiment promotion skipped: %s", exc)
        record_source_status(
            conn, refresh_id=refresh_id, source=SOURCE_MACHINE_EXPERIMENTS,
            status="error", reason=str(exc), row_count=0,
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
