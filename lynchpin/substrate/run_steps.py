"""Durable refresh-step observability for the DuckDB substrate."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import resource
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb


log = logging.getLogger(__name__)


def _process_io_bytes() -> tuple[int | None, int | None]:
    """Return Linux process read/write counters without adding a telemetry store."""
    try:
        values = {
            key: int(value)
            for key, value in (
                line.split(":", 1)
                for line in Path("/proc/self/io").read_text(encoding="utf-8").splitlines()
            )
        }
    except (OSError, ValueError):
        return None, None
    return values.get("read_bytes"), values.get("write_bytes")


class PhaseMeasurement:
    """Process-local wall, CPU, and IO deltas for one promotion phase."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: datetime | None = None
        self._wall_started = time.monotonic()
        self._usage_started = resource.getrusage(resource.RUSAGE_SELF)
        self._read_started, self._write_started = _process_io_bytes()

    def finish(self) -> None:
        if self.finished_at is not None:
            return
        self.finished_at = datetime.now(timezone.utc)
        usage_finished = resource.getrusage(resource.RUSAGE_SELF)
        read_finished, write_finished = _process_io_bytes()
        self.wall_seconds = round(time.monotonic() - self._wall_started, 6)
        self.cpu_user_seconds = round(usage_finished.ru_utime - self._usage_started.ru_utime, 6)
        self.cpu_system_seconds = round(usage_finished.ru_stime - self._usage_started.ru_stime, 6)
        self.read_bytes = _delta(self._read_started, read_finished)
        self.write_bytes = _delta(self._write_started, write_finished)

    def payload(self, *, count: int | None) -> dict[str, object]:
        if self.finished_at is None:
            raise RuntimeError(f"phase {self.phase} has not finished")
        return {
            "schema": "lynchpin.incremental-phase.v1",
            "phase": self.phase,
            "count": count,
            "wall_seconds": self.wall_seconds,
            "cpu_user_seconds": self.cpu_user_seconds,
            "cpu_system_seconds": self.cpu_system_seconds,
            "read_bytes": self.read_bytes,
            "write_bytes": self.write_bytes,
        }

    def __enter__(self) -> "PhaseMeasurement":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.finish()


def _delta(start: int | None, end: int | None) -> int | None:
    return end - start if start is not None and end is not None else None


def measure_phase(phase: str) -> PhaseMeasurement:
    """Measure a phase before persisting it to the existing run-step receipt."""
    return PhaseMeasurement(phase)


def record_phase_evidence(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    measurement: PhaseMeasurement,
    count: int | None,
) -> None:
    """Persist machine-readable phase evidence without a new telemetry database."""
    payload = measurement.payload(count=count)
    record_run_step(
        conn,
        refresh_id=refresh_id,
        step=f"incremental_{measurement.phase}",
        status="ok",
        message=json.dumps(payload, sort_keys=True),
        row_count=count,
        started_at=measurement.started_at,
        finished_at=measurement.finished_at,
    )


def log_phase_evidence(measurement: PhaseMeasurement, *, count: int | None) -> None:
    """Expose the same receipt shape to the materialization unit journal."""
    log.info("incremental_phase=%s", json.dumps(measurement.payload(count=count), sort_keys=True))


def reconcile_orphaned_running_steps(
    conn: "duckdb.DuckDBPyConnection",
    *,
    stale_before: datetime,
) -> int:
    """Mark stuck 'running' steps as orphaned so reads stop seeing them as live.

    substrate_run_step is append-only: a step records a 'running' row when it
    starts and a 'success'/'error' row when it finishes. If the process dies
    mid-step (killed, crashed — not a caught Python exception, which
    _run_stage already records as 'error'), nothing ever appends that
    terminal row, and any reader that takes the latest row per
    (refresh_id, step) sees a permanently misleading "still running" state
    (lynchpin-b5q). This appends an 'orphaned' terminal row for any step
    whose most recent row is still 'running' and was recorded before
    ``stale_before``, so the next read resolves to a real terminal status.

    Returns the number of steps reconciled.
    """
    rows = conn.execute(
        """
        SELECT refresh_id, step, started_at
        FROM (
            SELECT refresh_id, step, status, started_at,
                   row_number() OVER (
                       PARTITION BY refresh_id, step
                       ORDER BY
                           recorded_at DESC,
                           CASE WHEN status = 'running' THEN 0 ELSE 1 END DESC
                   ) AS rn
            FROM substrate_run_step
        )
        WHERE rn = 1 AND status = 'running' AND started_at < ?
        """,
        [stale_before],
    ).fetchall()
    finished_at = datetime.now(timezone.utc)
    for refresh_id, step, started_at in rows:
        record_run_step(
            conn,
            refresh_id=refresh_id,
            step=step,
            status="orphaned",
            message=(
                "reconciled: no terminal status was ever recorded for this "
                "step and it has been stale since before this check; the "
                "run that started it most likely died mid-step (killed or "
                "crashed) rather than completing"
            ),
            started_at=started_at,
            finished_at=finished_at,
        )
    return len(rows)


def record_run_step(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    step: str,
    status: str,
    message: str | None = None,
    row_count: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Append one progress/status row for a materialization or promotion step."""
    conn.execute(
        """
        INSERT INTO substrate_run_step
        (refresh_id, step, status, message, row_count, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            step,
            status,
            message,
            row_count,
            started_at,
            finished_at,
        ],
    )


__all__ = [
    "PhaseMeasurement",
    "log_phase_evidence",
    "measure_phase",
    "record_phase_evidence",
    "record_run_step",
    "reconcile_orphaned_running_steps",
]
