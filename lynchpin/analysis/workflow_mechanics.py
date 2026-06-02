"""Workflow mechanics over promoted development work observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lynchpin.core.io import save_json
from lynchpin.substrate.connection import connect, substrate_path

if TYPE_CHECKING:
    import duckdb


@dataclass(frozen=True)
class WorkflowCommandSummary:
    project: str | None
    command_key: str
    invocation_count: int
    failure_count: int
    success_count: int
    median_duration_s: float | None
    p95_duration_s: float | None
    retry_chain_count: int


@dataclass(frozen=True)
class WorkflowRetryChain:
    project: str | None
    command_key: str
    start: datetime
    end: datetime
    attempt_count: int
    failure_count: int
    final_status: str
    total_duration_s: float
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowMechanicsReport:
    start: date | None
    end: date | None
    invocation_count: int
    failure_count: int
    retry_chain_count: int
    command_summaries: tuple[WorkflowCommandSummary, ...]
    retry_chains: tuple[WorkflowRetryChain, ...]
    caveats: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def analyze_workflow_mechanics(
    *,
    start: date | None = None,
    end: date | None = None,
    project: str | None = None,
    refresh_id: str | None = None,
    path: str | None = None,
    retry_gap_min: int = 20,
    limit: int = 100,
) -> WorkflowMechanicsReport:
    with connect(path or substrate_path(), read_only=True) as conn:
        rows = _load_invocations(
            conn,
            start=start,
            end=end,
            project=project,
            refresh_id=refresh_id,
            limit=max(limit, 1) * 50,
        )
    chains = _retry_chains(rows, gap=timedelta(minutes=max(retry_gap_min, 1)))
    summaries = _command_summaries(rows, chains)
    summaries.sort(
        key=lambda row: (-row.retry_chain_count, -row.failure_count, -row.invocation_count)
    )
    chains.sort(key=lambda row: (-row.failure_count, -row.attempt_count, row.start))
    return WorkflowMechanicsReport(
        start=start,
        end=end,
        invocation_count=len(rows),
        failure_count=sum(1 for row in rows if _failed(row["status"], row["exit_code"])),
        retry_chain_count=len(chains),
        command_summaries=tuple(summaries[:limit]),
        retry_chains=tuple(chains[:limit]),
        caveats=(
            "retry chains are temporal heuristics over promoted work observations",
            "same command/project within retry_gap_min is treated as one loop; semantic intent is not inferred",
        ),
    )


def write_workflow_mechanics_report(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    project: str | None = None,
    refresh_id: str | None = None,
    path: str | None = None,
    retry_gap_min: int = 20,
    limit: int = 100,
) -> WorkflowMechanicsReport:
    report = analyze_workflow_mechanics(
        start=start,
        end=end,
        project=project,
        refresh_id=refresh_id,
        path=path,
        retry_gap_min=retry_gap_min,
        limit=limit,
    )
    payload = {
        "generated_at_utc": datetime.now().astimezone().isoformat(),
        **report.to_json(),
    }
    save_json(out, payload, sort_keys=True)
    return report


def _load_invocations(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None,
    end: date | None,
    project: str | None,
    refresh_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    else:
        latest = conn.execute(
            "SELECT refresh_id FROM work_observation ORDER BY materialized_at DESC LIMIT 1"
        ).fetchone()
        if latest:
            clauses.append("refresh_id = ?")
            params.append(latest[0])
    if start is not None:
        clauses.append("CAST(started_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(started_at AS DATE) < ?")
        params.append(end)
    if project is not None:
        clauses.append("project = ?")
        params.append(project)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(min(max(limit, 1), 100_000))
    rows = conn.execute(
        f"""
        SELECT source_id, project, command, started_at, ended_at, duration_s,
               status, exit_code, work_kind, live_stage
        FROM work_observation
        {where}
        ORDER BY started_at, source_id
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [
        {
            "source_id": row[0],
            "project": row[1],
            "command": tuple(row[2] or ()),
            "started_at": row[3],
            "ended_at": row[4],
            "duration_s": float(row[5]) if row[5] is not None else None,
            "status": row[6],
            "exit_code": row[7],
            "work_kind": row[8],
            "live_stage": row[9],
        }
        for row in rows
    ]


def _retry_chains(rows: list[dict[str, Any]], *, gap: timedelta) -> list[WorkflowRetryChain]:
    grouped: dict[tuple[str | None, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("project"), _command_key(row))].append(row)
    chains: list[WorkflowRetryChain] = []
    for (project, command_key), items in grouped.items():
        current: list[dict[str, Any]] = []
        for row in sorted(items, key=lambda item: item["started_at"]):
            if current and row["started_at"] - current[-1]["started_at"] > gap:
                _append_chain(chains, project, command_key, current)
                current = []
            current.append(row)
        _append_chain(chains, project, command_key, current)
    return chains


def _append_chain(
    chains: list[WorkflowRetryChain],
    project: str | None,
    command_key: str,
    items: list[dict[str, Any]],
) -> None:
    if len(items) < 2:
        return
    failures = [row for row in items[:-1] if _failed(row["status"], row["exit_code"])]
    if not failures:
        return
    start = items[0]["started_at"]
    end = items[-1]["ended_at"] or items[-1]["started_at"]
    chains.append(
        WorkflowRetryChain(
            project=project,
            command_key=command_key,
            start=start,
            end=end,
            attempt_count=len(items),
            failure_count=len(failures),
            final_status=str(items[-1]["status"]),
            total_duration_s=round(sum(row["duration_s"] or 0.0 for row in items), 3),
            source_ids=tuple(str(row["source_id"]) for row in items),
        )
    )


def _command_summaries(
    rows: list[dict[str, Any]],
    chains: list[WorkflowRetryChain],
) -> list[WorkflowCommandSummary]:
    by_key: dict[tuple[str | None, str], list[dict[str, Any]]] = defaultdict(list)
    retry_counts = Counter((chain.project, chain.command_key) for chain in chains)
    for row in rows:
        by_key[(row.get("project"), _command_key(row))].append(row)
    summaries = []
    for (project, command_key), items in by_key.items():
        durations = sorted(
            row["duration_s"] for row in items if row.get("duration_s") is not None
        )
        failures = sum(1 for row in items if _failed(row["status"], row["exit_code"]))
        summaries.append(
            WorkflowCommandSummary(
                project=project,
                command_key=command_key,
                invocation_count=len(items),
                failure_count=failures,
                success_count=len(items) - failures,
                median_duration_s=_median(durations),
                p95_duration_s=_p95(durations),
                retry_chain_count=retry_counts[(project, command_key)],
            )
        )
    return summaries


def _command_key(row: dict[str, Any]) -> str:
    command = row.get("command") or ()
    if command:
        return " ".join(str(part) for part in command[:3])
    return str(row.get("work_kind") or row.get("live_stage") or "unknown")


def _failed(status: Any, exit_code: Any) -> bool:
    if exit_code not in {None, 0}:
        return True
    return str(status or "").lower() not in {"ok", "success", "completed", "pass", "passed"}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return round(values[mid], 3)
    return round((values[mid - 1] + values[mid]) / 2.0, 3)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    idx = min(len(values) - 1, int((len(values) - 1) * 0.95))
    return round(values[idx], 3)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


__all__ = [
    "WorkflowCommandSummary",
    "WorkflowMechanicsReport",
    "WorkflowRetryChain",
    "analyze_workflow_mechanics",
    "write_workflow_mechanics_report",
]
