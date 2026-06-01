"""Read models over timed work observations and xtask child timings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lynchpin.core.io import save_json
from lynchpin.substrate.connection import connect, substrate_path

from .sql import latest_machine_rows

if TYPE_CHECKING:
    import duckdb


@dataclass(frozen=True)
class WorkObservationDaily:
    date: date
    work_kind: str
    project: str | None
    command: tuple[str, ...]
    observation_count: int
    success_count: int
    failed_count: int
    avg_duration_s: float | None
    median_duration_s: float | None
    p95_duration_s: float | None
    max_duration_s: float | None


@dataclass(frozen=True)
class WorkStageSummary:
    stage_name: str
    observation_count: int
    success_count: int
    avg_duration_s: float | None
    median_duration_s: float | None
    p95_duration_s: float | None
    max_duration_s: float | None


@dataclass(frozen=True)
class WorkTestSummary:
    package: str | None
    status: str
    test_count: int
    avg_duration_s: float | None
    median_duration_s: float | None
    p95_duration_s: float | None
    max_duration_s: float | None


@dataclass(frozen=True)
class WorkFailureSummary:
    failure_kind: str
    project: str | None
    package: str | None
    stage_name: str | None
    status: str | None
    failure_type: str | None
    exit_code: int | None
    failure_count: int
    affected_invocation_count: int
    median_duration_s: float | None
    max_duration_s: float | None


def analyze_work_observations(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    with connect(path or substrate_path(), read_only=True) as conn:
        daily = daily_work_observation_series(
            conn,
            refresh_id=refresh_id,
            start=start,
            end=end,
        )
        check_daily = daily_work_observation_series(
            conn,
            refresh_id=refresh_id,
            start=start,
            end=end,
            project="sinex",
            command_contains="check",
        )
        stages = stage_timing_summary(
            conn,
            refresh_id=refresh_id,
            start=start,
            end=end,
            limit=25,
        )
        tests = test_duration_summary(
            conn,
            refresh_id=refresh_id,
            start=start,
            end=end,
            limit=25,
        )
        failures = failure_taxonomy_summary(
            conn,
            refresh_id=refresh_id,
            start=start,
            end=end,
            limit=50,
        )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
        "refresh_id": refresh_id,
        "daily": [_asdict(row) for row in daily],
        "sinex_check_daily": [_asdict(row) for row in check_daily],
        "stage_summaries": [_asdict(row) for row in stages],
        "test_summaries": [_asdict(row) for row in tests],
        "failure_summaries": [_asdict(row) for row in failures],
        "caveats": [
            "observational xtask history; not a controlled benchmark",
            "test result rows do not carry independent timestamps; date filtering is by promotion window",
            "invocation_packages is package membership only and is not represented as timed package evidence",
        ],
    }


def write_work_observation_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    payload = analyze_work_observations(
        start=start,
        end=end,
        path=path,
        refresh_id=refresh_id,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return payload


def daily_work_observation_series(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    project: str | None = None,
    command_contains: str | None = None,
) -> list[WorkObservationDaily]:
    where, params = _where(
        refresh_id=refresh_id,
        start=start,
        end=end,
        project=project,
        command_contains=command_contains,
        time_column="started_at",
    )
    source = _source_sql("work_observation", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT
            CAST(started_at AS DATE) AS date,
            work_kind,
            project,
            command,
            COUNT(*) AS observation_count,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS failed_count,
            AVG(duration_s) AS avg_duration_s,
            MEDIAN(duration_s) AS median_duration_s,
            QUANTILE_CONT(duration_s, 0.95) AS p95_duration_s,
            MAX(duration_s) AS max_duration_s
        FROM ({source})
        {where}
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
        """,
        params,
    ).fetchall()
    return [
        WorkObservationDaily(
            date=row[0],
            work_kind=str(row[1]),
            project=row[2],
            command=tuple(row[3] or ()),
            observation_count=int(row[4]),
            success_count=int(row[5] or 0),
            failed_count=int(row[6] or 0),
            avg_duration_s=_float(row[7]),
            median_duration_s=_float(row[8]),
            p95_duration_s=_float(row[9]),
            max_duration_s=_float(row[10]),
        )
        for row in rows
    ]


def stage_timing_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    stage_name: str | None = None,
    limit: int = 100,
) -> list[WorkStageSummary]:
    clauses, params = _base_clauses(refresh_id=refresh_id, start=start, end=end, time_column="started_at")
    if stage_name is not None:
        clauses.append("stage_name = ?")
        params.append(stage_name)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(_limit(limit))
    source = _source_sql("work_observation_stage", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT
            stage_name,
            COUNT(*) AS observation_count,
            SUM(CASE WHEN success THEN 1 ELSE 0 END) AS success_count,
            AVG(duration_s) AS avg_duration_s,
            MEDIAN(duration_s) AS median_duration_s,
            QUANTILE_CONT(duration_s, 0.95) AS p95_duration_s,
            MAX(duration_s) AS max_duration_s
        FROM ({source})
        {where}
        GROUP BY 1
        ORDER BY max_duration_s DESC NULLS LAST, observation_count DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        WorkStageSummary(
            stage_name=str(row[0]),
            observation_count=int(row[1]),
            success_count=int(row[2] or 0),
            avg_duration_s=_float(row[3]),
            median_duration_s=_float(row[4]),
            p95_duration_s=_float(row[5]),
            max_duration_s=_float(row[6]),
        )
        for row in rows
    ]


def test_duration_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    package: str | None = None,
    limit: int = 100,
) -> list[WorkTestSummary]:
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    if package is not None:
        clauses.append("package = ?")
        params.append(package)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(_limit(limit))
    # Test rows do not carry execution timestamps in the xtask schema; callers
    # should filter by refresh/source window at promotion time.
    source = _source_sql("work_observation_test_result", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT
            package,
            status,
            COUNT(*) AS test_count,
            AVG(duration_s) AS avg_duration_s,
            MEDIAN(duration_s) AS median_duration_s,
            QUANTILE_CONT(duration_s, 0.95) AS p95_duration_s,
            MAX(duration_s) AS max_duration_s
        FROM ({source})
        {where}
        GROUP BY 1, 2
        ORDER BY max_duration_s DESC NULLS LAST, test_count DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        WorkTestSummary(
            package=row[0],
            status=str(row[1]),
            test_count=int(row[2]),
            avg_duration_s=_float(row[3]),
            median_duration_s=_float(row[4]),
            p95_duration_s=_float(row[5]),
            max_duration_s=_float(row[6]),
        )
        for row in rows
    ]


def failure_taxonomy_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
) -> list[WorkFailureSummary]:
    rows = [
        *_invocation_failure_summary(conn, refresh_id=refresh_id, start=start, end=end, limit=limit),
        *_stage_failure_summary(conn, refresh_id=refresh_id, start=start, end=end, limit=limit),
        *_test_failure_summary(conn, refresh_id=refresh_id, start=start, end=end, limit=limit),
    ]
    rows.sort(key=lambda row: (-row.failure_count, row.failure_kind, row.project or "", row.package or "", row.stage_name or "", row.status or ""))
    return rows[:_limit(limit)]


def _invocation_failure_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None,
    start: date | None,
    end: date | None,
    limit: int,
) -> list[WorkFailureSummary]:
    where, params = _where(
        refresh_id=refresh_id,
        start=start,
        end=end,
        project=None,
        command_contains=None,
        time_column="started_at",
    )
    where = f"{where} AND status != 'success'" if where else "WHERE status != 'success'"
    params.append(_limit(limit))
    source = _source_sql("work_observation", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT project, status, exit_code,
               COUNT(*) AS failure_count,
               COUNT(DISTINCT source_id) AS affected_invocation_count,
               MEDIAN(duration_s) AS median_duration_s,
               MAX(duration_s) AS max_duration_s
        FROM ({source})
        {where}
        GROUP BY 1, 2, 3
        ORDER BY failure_count DESC, max_duration_s DESC NULLS LAST
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        WorkFailureSummary(
            failure_kind="invocation",
            project=row[0],
            package=None,
            stage_name=None,
            status=row[1],
            failure_type=None,
            exit_code=int(row[2]) if row[2] is not None else None,
            failure_count=int(row[3]),
            affected_invocation_count=int(row[4]),
            median_duration_s=_float(row[5]),
            max_duration_s=_float(row[6]),
        )
        for row in rows
    ]


def _stage_failure_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None,
    start: date | None,
    end: date | None,
    limit: int,
) -> list[WorkFailureSummary]:
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("s.refresh_id = ?")
        params.append(refresh_id)
    if start is not None:
        clauses.append("CAST(s.started_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(s.started_at AS DATE) < ?")
        params.append(end)
    clauses.append("s.success = false")
    where = f"WHERE {' AND '.join(clauses)}"
    params.append(_limit(limit))
    stages = _source_sql("work_observation_stage", refresh_id=refresh_id)
    invocations = _source_sql("work_observation", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT w.project, s.stage_name,
               COUNT(*) AS failure_count,
               COUNT(DISTINCT s.invocation_source_id) AS affected_invocation_count,
               MEDIAN(s.duration_s) AS median_duration_s,
               MAX(s.duration_s) AS max_duration_s
        FROM ({stages}) s
        LEFT JOIN ({invocations}) w
          ON w.source = s.source
         AND w.source_id = s.invocation_source_id
         AND w.refresh_id = s.refresh_id
        {where}
        GROUP BY 1, 2
        ORDER BY failure_count DESC, max_duration_s DESC NULLS LAST
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        WorkFailureSummary(
            failure_kind="stage",
            project=row[0],
            package=None,
            stage_name=row[1],
            status="failed",
            failure_type=None,
            exit_code=None,
            failure_count=int(row[2]),
            affected_invocation_count=int(row[3]),
            median_duration_s=_float(row[4]),
            max_duration_s=_float(row[5]),
        )
        for row in rows
    ]


def _test_failure_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None,
    start: date | None,
    end: date | None,
    limit: int,
) -> list[WorkFailureSummary]:
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("t.refresh_id = ?")
        params.append(refresh_id)
    if start is not None:
        clauses.append("CAST(w.started_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(w.started_at AS DATE) < ?")
        params.append(end)
    clauses.append("t.status NOT IN ('pass', 'success')")
    params.append(_limit(limit))
    tests = _source_sql("work_observation_test_result", refresh_id=refresh_id)
    invocations = _source_sql("work_observation", refresh_id=refresh_id)
    rows = conn.execute(
        f"""
        SELECT w.project, t.package, t.status, t.failure_type,
               COUNT(*) AS failure_count,
               COUNT(DISTINCT t.invocation_source_id) AS affected_invocation_count,
               MEDIAN(t.duration_s) AS median_duration_s,
               MAX(t.duration_s) AS max_duration_s
        FROM ({tests}) t
        LEFT JOIN ({invocations}) w
          ON w.source = t.source
         AND w.source_id = t.invocation_source_id
         AND w.refresh_id = t.refresh_id
        WHERE {" AND ".join(clauses)}
        GROUP BY 1, 2, 3, 4
        ORDER BY failure_count DESC, max_duration_s DESC NULLS LAST
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        WorkFailureSummary(
            failure_kind="test",
            project=row[0],
            package=row[1],
            stage_name=None,
            status=row[2],
            failure_type=row[3],
            exit_code=None,
            failure_count=int(row[4]),
            affected_invocation_count=int(row[5]),
            median_duration_s=_float(row[6]),
            max_duration_s=_float(row[7]),
        )
        for row in rows
    ]


def _where(
    *,
    refresh_id: str | None,
    start: date | None,
    end: date | None,
    project: str | None,
    command_contains: str | None,
    time_column: str,
) -> tuple[str, list[Any]]:
    clauses, params = _base_clauses(
        refresh_id=refresh_id,
        start=start,
        end=end,
        time_column=time_column,
    )
    if project is not None:
        clauses.append("project = ?")
        params.append(project)
    if command_contains is not None:
        clauses.append("list_contains(command, ?)")
        params.append(command_contains)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)


def _base_clauses(
    *,
    refresh_id: str | None,
    start: date | None,
    end: date | None,
    time_column: str,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    if start is not None:
        clauses.append(f"CAST({time_column} AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append(f"CAST({time_column} AS DATE) < ?")
        params.append(end)
    return clauses, params


def _limit(value: int) -> int:
    return min(max(int(value), 1), 10_000)


def _source_sql(table: str, *, refresh_id: str | None) -> str:
    if refresh_id is not None:
        return f"SELECT * FROM {table}"
    return latest_machine_rows(table)


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _asdict(row: Any) -> dict[str, Any]:
    return row.__dict__


__all__ = [
    "WorkObservationDaily",
    "WorkFailureSummary",
    "WorkStageSummary",
    "WorkTestSummary",
    "daily_work_observation_series",
    "failure_taxonomy_summary",
    "analyze_work_observations",
    "stage_timing_summary",
    "test_duration_summary",
    "write_work_observation_analysis",
]
