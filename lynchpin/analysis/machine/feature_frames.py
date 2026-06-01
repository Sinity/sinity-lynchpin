"""Leakage-aware analysis feature frames for machine attribution mining."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lynchpin.core.io import save_json
from lynchpin.substrate.connection import connect, substrate_path

from .sql import latest_machine_rows

if TYPE_CHECKING:
    import duckdb


PRESSURE_COLUMNS = (
    "host_cpu_pressure_some_avg10_max",
    "host_io_pressure_some_avg10_max",
    "host_io_pressure_full_avg10_max",
    "host_memory_pressure_some_avg10_max",
    "host_memory_pressure_full_avg10_max",
)
OUTCOME_COLUMNS = ("stage.duration_s", "invocation.duration_s")
EXPOSURE_COLUMNS = PRESSURE_COLUMNS
COVARIATE_COLUMNS = (
    "work_kind",
    "stage_name",
    "live_stage",
    "command",
    "project",
    "cwd",
    "host",
    "git_commit",
    "git_dirty",
    "invocation_status",
    "invocation_exit_code",
    *PRESSURE_COLUMNS,
)


@dataclass(frozen=True)
class MachineAnalysisFeatureRow:
    frame_id: str
    unit_type: str
    unit_id: str
    parent_unit_id: str | None
    project: str | None
    outcome_metric: str
    outcome_value: float | None
    outcome_window_start: datetime | None
    outcome_window_end: datetime | None
    exposure_window_start: datetime | None
    exposure_window_end: datetime | None
    exposure_policy: str
    covariates: dict[str, Any]
    missingness: dict[str, bool]
    censoring_status: str
    leakage_status: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineAnalysisFeatureFrame:
    frame_id: str
    unit_type: str
    row_count: int
    outcome_metric: str
    outcome_columns: tuple[str, ...]
    exposure_columns: tuple[str, ...]
    covariate_columns: tuple[str, ...]
    leakage_status: str
    missing_value_count: int
    missingness_summary: dict[str, int]
    censored_count: int
    censoring_summary: dict[str, int]
    leakage_summary: dict[str, int]
    source_refresh_ids: tuple[str, ...]
    rows: list[MachineAnalysisFeatureRow]
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_feature_frames(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    refresh_id: str | None = None,
    unit_type: str = "work_observation_stage",
    limit: int = 10_000,
) -> MachineAnalysisFeatureFrame:
    if unit_type not in {"work_observation_stage", "work_observation"}:
        raise ValueError(f"unsupported machine feature-frame unit_type: {unit_type}")
    with connect(path or substrate_path(), read_only=True) as conn:
        if unit_type == "work_observation":
            rows = _invocation_rows(
                conn,
                start=start,
                end=end,
                refresh_id=refresh_id,
                limit=limit,
            )
            source_table = "work_observation"
        else:
            rows = _stage_rows(
                conn,
                start=start,
                end=end,
                refresh_id=refresh_id,
                limit=limit,
            )
            source_table = "work_observation_stage"
        source_refresh_ids = _source_refresh_ids(
            conn,
            table=source_table,
            refresh_id=refresh_id,
        )
    missing_value_count = sum(sum(row.missingness.values()) for row in rows)
    censored_count = sum(1 for row in rows if row.censoring_status != "observed")
    missingness_summary = {
        column: sum(1 for row in rows if row.missingness.get(column))
        for column in COVARIATE_COLUMNS
    }
    censoring_summary = _count_by(row.censoring_status for row in rows)
    leakage_summary = _count_by(row.leakage_status for row in rows)
    caveats = [
        f"{unit_type} feature frame is observational and cannot create causal support",
        "pressure covariates overlapping the outcome are concurrent context, not pre-treatment adjustment variables",
    ]
    if any(row.leakage_status != "ok" for row in rows):
        caveats.append("one or more rows failed leakage validation")
    return MachineAnalysisFeatureFrame(
        frame_id=_frame_id(unit_type, start, end, refresh_id),
        unit_type=unit_type,
        row_count=len(rows),
        outcome_metric=_outcome_metric(unit_type),
        outcome_columns=(_outcome_metric(unit_type),),
        exposure_columns=EXPOSURE_COLUMNS,
        covariate_columns=COVARIATE_COLUMNS,
        leakage_status="ok" if all(row.leakage_status == "ok" for row in rows) else "invalid",
        missing_value_count=missing_value_count,
        missingness_summary=missingness_summary,
        censored_count=censored_count,
        censoring_summary=censoring_summary,
        leakage_summary=leakage_summary,
        source_refresh_ids=source_refresh_ids,
        rows=rows,
        caveats=tuple(caveats),
    )


def write_machine_feature_frames(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    refresh_id: str | None = None,
    unit_type: str = "work_observation_stage",
    limit: int = 10_000,
) -> MachineAnalysisFeatureFrame:
    frame = analyze_machine_feature_frames(
        start=start,
        end=end,
        path=path,
        refresh_id=refresh_id,
        unit_type=unit_type,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "frame": json.loads(json.dumps(frame.to_dict(), default=str)),
    }
    save_json(out, payload, sort_keys=True)
    return frame


def _invocation_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None,
    end: date | None,
    refresh_id: str | None,
    limit: int,
) -> list[MachineAnalysisFeatureRow]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("CAST(started_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(started_at AS DATE) < ?")
        params.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(min(max(int(limit), 1), 100_000))
    invocations = _source_sql("work_observation", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT
            source_id,
            work_kind,
            project,
            command,
            cwd,
            started_at,
            ended_at,
            duration_s,
            status,
            exit_code,
            host,
            git_commit,
            git_dirty,
            live_stage,
            host_cpu_pressure_some_avg10_max,
            host_io_pressure_some_avg10_max,
            host_io_pressure_full_avg10_max,
            host_memory_pressure_some_avg10_max,
            host_memory_pressure_full_avg10_max
        FROM ({invocations})
        {where}
        ORDER BY started_at, source_id
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_invocation_feature_row(row) for row in rows]


def _stage_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None,
    end: date | None,
    refresh_id: str | None,
    limit: int,
) -> list[MachineAnalysisFeatureRow]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("CAST(s.started_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(s.started_at AS DATE) < ?")
        params.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(min(max(int(limit), 1), 100_000))
    stages = _source_sql("work_observation_stage", refresh_id=refresh_id)
    invocations = _source_sql("work_observation", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT
            s.source_id,
            s.invocation_source_id,
            s.stage_name,
            s.started_at,
            s.duration_s,
            s.success,
            i.project,
            i.command,
            i.status,
            i.exit_code,
            i.host,
            i.git_commit,
            i.git_dirty,
            i.host_cpu_pressure_some_avg10_max,
            i.host_io_pressure_some_avg10_max,
            i.host_io_pressure_full_avg10_max,
            i.host_memory_pressure_some_avg10_max,
            i.host_memory_pressure_full_avg10_max
        FROM ({stages}) s
        LEFT JOIN ({invocations}) i
          ON i.source = s.source
         AND i.source_id = s.invocation_source_id
        {where}
        ORDER BY s.started_at, s.source_id
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_stage_feature_row(row) for row in rows]


def _stage_feature_row(row: Any) -> MachineAnalysisFeatureRow:
    (
        source_id,
        invocation_source_id,
        stage_name,
        started_at,
        duration_s,
        success,
        project,
        command,
        status,
        exit_code,
        host,
        git_commit,
        git_dirty,
        cpu_pressure,
        io_some,
        io_full,
        mem_some,
        mem_full,
    ) = row
    duration = _float(duration_s)
    outcome_start = _datetime(started_at)
    outcome_end = outcome_start + timedelta(seconds=duration) if outcome_start is not None and duration is not None else None
    covariates = {
        "work_kind": "stage",
        "stage_name": stage_name,
        "live_stage": None,
        "command": tuple(command or ()),
        "project": project,
        "cwd": None,
        "host": host,
        "git_commit": git_commit,
        "git_dirty": bool(git_dirty) if git_dirty is not None else None,
        "invocation_status": status,
        "invocation_exit_code": exit_code,
        "host_cpu_pressure_some_avg10_max": _float(cpu_pressure),
        "host_io_pressure_some_avg10_max": _float(io_some),
        "host_io_pressure_full_avg10_max": _float(io_full),
        "host_memory_pressure_some_avg10_max": _float(mem_some),
        "host_memory_pressure_full_avg10_max": _float(mem_full),
    }
    missingness = {key: value is None for key, value in covariates.items()}
    caveats = ["concurrent pressure features are context, not pre-treatment controls"]
    if invocation_source_id is None:
        caveats.append("parent invocation missing; project/command covariates may be absent")
    return MachineAnalysisFeatureRow(
        frame_id=_row_id(source_id, stage_name, started_at),
        unit_type="work_observation_stage",
        unit_id=str(source_id),
        parent_unit_id=str(invocation_source_id) if invocation_source_id is not None else None,
        project=str(project) if project is not None else None,
        outcome_metric="stage.duration_s",
        outcome_value=duration,
        outcome_window_start=outcome_start,
        outcome_window_end=outcome_end,
        exposure_window_start=outcome_start,
        exposure_window_end=outcome_end,
        exposure_policy="concurrent_context",
        covariates=covariates,
        missingness=missingness,
        censoring_status="observed" if bool(success) else "failed_or_cancelled",
        leakage_status=_leakage_status(outcome_start, outcome_end),
        caveats=tuple(caveats),
    )


def _invocation_feature_row(row: Any) -> MachineAnalysisFeatureRow:
    (
        source_id,
        work_kind,
        project,
        command,
        cwd,
        started_at,
        ended_at,
        duration_s,
        status,
        exit_code,
        host,
        git_commit,
        git_dirty,
        live_stage,
        cpu_pressure,
        io_some,
        io_full,
        mem_some,
        mem_full,
    ) = row
    duration = _float(duration_s)
    outcome_start = _datetime(started_at)
    outcome_end = _datetime(ended_at)
    if outcome_end is None and outcome_start is not None and duration is not None:
        outcome_end = outcome_start + timedelta(seconds=duration)
    covariates = {
        "work_kind": work_kind,
        "stage_name": None,
        "live_stage": live_stage,
        "command": tuple(command or ()),
        "project": project,
        "cwd": cwd,
        "host": host,
        "git_commit": git_commit,
        "git_dirty": bool(git_dirty) if git_dirty is not None else None,
        "invocation_status": status,
        "invocation_exit_code": exit_code,
        "host_cpu_pressure_some_avg10_max": _float(cpu_pressure),
        "host_io_pressure_some_avg10_max": _float(io_some),
        "host_io_pressure_full_avg10_max": _float(io_full),
        "host_memory_pressure_some_avg10_max": _float(mem_some),
        "host_memory_pressure_full_avg10_max": _float(mem_full),
    }
    missingness = {key: value is None for key, value in covariates.items()}
    caveats = ["concurrent pressure features are context, not pre-treatment controls"]
    observed_statuses = {"ok", "success", "completed", "pass", "passed"}
    return MachineAnalysisFeatureRow(
        frame_id=_row_id(source_id, work_kind, started_at),
        unit_type="work_observation",
        unit_id=str(source_id),
        parent_unit_id=None,
        project=str(project) if project is not None else None,
        outcome_metric="invocation.duration_s",
        outcome_value=duration,
        outcome_window_start=outcome_start,
        outcome_window_end=outcome_end,
        exposure_window_start=outcome_start,
        exposure_window_end=outcome_end,
        exposure_policy="concurrent_context",
        covariates=covariates,
        missingness=missingness,
        censoring_status=(
            "observed" if str(status).lower() in observed_statuses else "failed_or_cancelled"
        ),
        leakage_status=_leakage_status(outcome_start, outcome_end),
        caveats=tuple(caveats),
    )


def _outcome_metric(unit_type: str) -> str:
    if unit_type == "work_observation":
        return "invocation.duration_s"
    return "stage.duration_s"


def _source_sql(table: str, *, refresh_id: str | None) -> str:
    if refresh_id is not None:
        escaped = refresh_id.replace("'", "''")
        return f"SELECT * FROM {table} WHERE refresh_id = '{escaped}'"
    return latest_machine_rows(table)


def _source_refresh_ids(
    conn: "duckdb.DuckDBPyConnection",
    *,
    table: str,
    refresh_id: str | None,
) -> tuple[str, ...]:
    if refresh_id is not None:
        return (refresh_id,)
    rows = conn.execute(
        f"SELECT DISTINCT refresh_id FROM ({latest_machine_rows(table)}) ORDER BY refresh_id"
    ).fetchall()
    return tuple(str(row[0]) for row in rows if row and row[0] is not None)


def _leakage_status(started_at: datetime | None, ended_at: datetime | None) -> str:
    if started_at is None or ended_at is None:
        return "invalid_missing_window"
    if ended_at < started_at:
        return "invalid_negative_window"
    return "ok"


def _datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _frame_id(unit_type: str, start: date | None, end: date | None, refresh_id: str | None) -> str:
    return _digest("frame", unit_type, start, end, refresh_id)


def _row_id(*parts: Any) -> str:
    return _digest("feature-row", *parts)


def _count_by(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _digest(prefix: str, *parts: Any) -> str:
    raw = "\0".join("" if part is None else str(part) for part in parts)
    return f"machine-{prefix}:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


__all__ = [
    "MachineAnalysisFeatureFrame",
    "MachineAnalysisFeatureRow",
    "analyze_machine_feature_frames",
    "write_machine_feature_frames",
]
