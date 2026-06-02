"""Machine telemetry promotion for the refresh DAG substrate step.

Uses DuckDB's SQLite ATTACH to bulk-transfer machine tables directly
(no Python row-by-roundtrip).  Falls back to the iterator path when the
SQLite extension is unavailable or the canonical DB path is absent.
"""

from __future__ import annotations

from dataclasses import replace
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .substrate_promote_status import (
    SOURCE_MACHINE,
    SOURCE_MACHINE_EXPERIMENTS,
    SOURCE_MACHINE_GPU,
    SOURCE_MACHINE_NETWORK,
    SOURCE_MACHINE_SERVICE_STATE,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_machine_tables(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(*(
        SOURCE_MACHINE,
        SOURCE_MACHINE_GPU,
        SOURCE_MACHINE_NETWORK,
        SOURCE_MACHINE_SERVICE_STATE,
        SOURCE_MACHINE_EXPERIMENTS,
    )):
        return

    # ── fast path: DuckDB SQLite ATTACH ──────────────────────────────────
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
            )
            _promote_experiments(conn, refresh_id, window_start, window_end, counts, selection)
            return
        except Exception as exc:
            log.warning(
                "substrate_promote: fast machine promotion failed, "
                "falling back to Python iterator path: %s",
                exc,
            )

    # ── slow path: Python row-by-row (fallback) ─────────────────────────
    _promote_machine_slow(conn, refresh_id, window_start, window_end, counts, selection)
    _promote_experiments(conn, refresh_id, window_start, window_end, counts, selection)


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
        _GPU_SAMPLE_COLUMNS,
        _METRIC_SAMPLE_COLUMNS,
        _NETWORK_SAMPLE_COLUMNS,
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
    }


def _promote_machine_fast(
    conn: Any,
    *,
    refresh_id: str,
    sqlite_path: Path,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    """Bulk-transfer machine tables via DuckDB SQLite ATTACH.

    Avoids the Python-object roundtrip penalty — 1.5 M rows transfer in
    ~1.5 s instead of hours.
    """
    t_total = time.monotonic()
    conn.execute("INSTALL SQLITE")
    conn.execute("LOAD SQLITE")
    conn.execute(f"ATTACH '{sqlite_path}' AS machine_src (TYPE SQLITE)")

    from lynchpin.sources.machine import readiness as machine_readiness

    machine_ready = machine_readiness()
    date_filter, date_params = _source_window_filter(window_start, window_end)
    projections = _machine_projections()

    tables = [
        ("metric_sample", "machine_metric_sample", SOURCE_MACHINE, selection.includes(SOURCE_MACHINE)),
        ("service_state", "machine_service_state", SOURCE_MACHINE_SERVICE_STATE, selection.includes(SOURCE_MACHINE_SERVICE_STATE)),
        ("gpu_sample", "machine_gpu_sample", SOURCE_MACHINE_GPU, selection.includes(SOURCE_MACHINE_GPU)),
        ("network_sample", "machine_network_sample", SOURCE_MACHINE_NETWORK, selection.includes(SOURCE_MACHINE_NETWORK)),
    ]

    for src_table, dst_table, source, enabled in tables:
        if not enabled:
            continue
        t0 = time.monotonic()
        try:
            # Idempotent: DELETE existing rows for this refresh_id,
            # then INSERT fresh.
            conn.execute(
                f"DELETE FROM {dst_table} WHERE refresh_id = ?",
                [refresh_id],
            )
            columns, overrides = projections[src_table]
            select_exprs = ", ".join(f"{overrides.get(c, c)} AS {c}" for c in columns)
            conn.execute(
                f"INSERT INTO {dst_table} ({', '.join(columns)}, refresh_id) "
                f"SELECT {select_exprs}, ? AS refresh_id "
                f"FROM machine_src.{src_table} {date_filter}",
                [refresh_id, *date_params],
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
        promote_machine_service_states,
    )

    if not selection.includes(
        SOURCE_MACHINE,
        SOURCE_MACHINE_SERVICE_STATE,
        SOURCE_MACHINE_GPU,
        SOURCE_MACHINE_NETWORK,
    ):
        return

    try:
        from lynchpin.sources.machine import (
            gpu_samples,
            metric_samples,
            network_samples,
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
    except Exception as exc:
        log.warning("substrate_promote: machine telemetry promotion skipped: %s", exc)
        for source in (SOURCE_MACHINE, SOURCE_MACHINE_SERVICE_STATE, SOURCE_MACHINE_GPU, SOURCE_MACHINE_NETWORK):
            if selection.includes(source):
                record_source_status(
                    conn, refresh_id=refresh_id, source=source,
                    status="error", reason=str(exc), row_count=0,
                    window_start=window_start, window_end=window_end,
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
