"""Cheap status sidecar for the derived DuckDB substrate."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lynchpin.substrate.connection import substrate_path

SUBSTRATE_STATUS_DATASET = "evidence_graph_substrate"


def substrate_status_manifest_path(path: Path | None = None) -> Path:
    target = Path(path or substrate_path())
    return target.with_suffix(".manifest.json")


def load_current_substrate_status_manifest(path: Path | None = None) -> dict[str, Any] | None:
    target = Path(path or substrate_path())
    manifest_path = substrate_status_manifest_path(target)
    if not target.exists() or not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("dataset") != SUBSTRATE_STATUS_DATASET:
        return None
    stat = _file_stat(target)
    if stat is None:
        return None
    if manifest.get("substrate_size_bytes") != stat["size_bytes"]:
        return None
    if manifest.get("substrate_mtime_ns") != stat["mtime_ns"]:
        return None
    return manifest


def write_substrate_status_manifest(path: Path | None = None) -> dict[str, Any] | None:
    target = Path(path or substrate_path())
    if not target.exists():
        return None
    status = _read_substrate_status(target)
    stat = _file_stat(target)
    if stat is None:
        return None
    manifest = {
        "dataset": SUBSTRATE_STATUS_DATASET,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "substrate_path": str(target),
        "substrate_size_bytes": stat["size_bytes"],
        "substrate_mtime_ns": stat["mtime_ns"],
        **status,
    }
    manifest_path = substrate_status_manifest_path(target)
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _read_substrate_status(path: Path) -> dict[str, Any]:
    try:
        import duckdb

        conn = duckdb.connect(str(path), read_only=True)
        try:
            builds = _scalar_count(conn, "evidence_graph_build")
            latest_build = _latest_graph_build(conn)
            latest_status = _latest_source_status(conn, "evidence_graph")
            promotion_count = _successful_promotion_count(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - sidecar writer must not break promotion.
        return {
            "builds": None,
            "latest_node_count": None,
            "latest_edge_count": None,
            "latest_source_status": None,
            "latest_source_reason": f"{type(exc).__name__}: {exc}",
            "promotion_count": None,
            "status": "partial",
            "reason": "could not inspect substrate status",
            "row_count": None,
        }
    latest_node_count = latest_build["node_count"] if latest_build else None
    latest_edge_count = latest_build["edge_count"] if latest_build else None
    status, reason = _status_reason(builds, latest_node_count, latest_status, promotion_count)
    first_date = latest_build["start_date"] if latest_build else None
    end_date = latest_build["end_date"] if latest_build else None
    if first_date is not None and end_date is not None and end_date > first_date:
        last_date = end_date - timedelta(days=1)
    else:
        last_date = first_date
    return {
        "builds": builds,
        "latest_node_count": latest_node_count,
        "latest_edge_count": latest_edge_count,
        "first_date": first_date.isoformat() if first_date is not None else None,
        "last_date": last_date.isoformat() if last_date is not None else None,
        "covered_dates": [
            day.isoformat()
            for day in _iter_dates(first_date, end_date)
        ],
        "latest_source_status": latest_status[0] if latest_status else None,
        "latest_source_reason": latest_status[1] if latest_status else None,
        "promotion_count": promotion_count,
        "status": status,
        "reason": reason,
        "row_count": latest_node_count or builds or promotion_count,
    }


def _status_reason(
    builds: int | None,
    latest_node_count: int | None,
    latest_status: tuple[str, str | None] | None,
    promotion_count: int | None,
) -> tuple[str, str]:
    if builds and builds > 0 and latest_node_count and latest_node_count > 0:
        return "ready", "DuckDB evidence graph builds are present"
    if builds and builds > 0 and latest_node_count == 0:
        return "empty", "latest evidence graph build contains no nodes"
    if latest_status and latest_status[0] == "empty":
        return "empty", latest_status[1] or "latest evidence graph promotion produced no nodes"
    if latest_status and latest_status[0] == "error":
        return "degraded", latest_status[1] or "latest evidence graph promotion errored"
    if promotion_count and promotion_count > 0:
        return "ready", "DuckDB substrate promotion runs are present"
    return "partial", "no materialized evidence graph build recorded"


def _scalar_count(conn: Any, table: str) -> int | None:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except Exception:
        return None
    return int(row[0]) if row else None


def _latest_source_status(conn: Any, source: str) -> tuple[str, str | None] | None:
    try:
        row = conn.execute(
            """
            SELECT status, reason
            FROM substrate_source_status
            WHERE source = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            [source],
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return str(row[0]), row[1]


def _successful_promotion_count(conn: Any) -> int | None:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM substrate_promotion_run
            WHERE status = 'ok'
            """
        ).fetchone()
    except Exception:
        return None
    return int(row[0]) if row else None


def _latest_graph_build(conn: Any) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            """
            SELECT start_date, end_date, node_count, edge_count
            FROM evidence_graph_build
            ORDER BY materialized_at DESC, generated_at DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {
        "start_date": row[0],
        "end_date": row[1],
        "node_count": int(row[2]),
        "edge_count": int(row[3]),
    }


def _iter_dates(start: date | None, end: date | None) -> tuple[date, ...]:
    if start is None or end is None or end <= start:
        return ()
    days: list[date] = []
    current = start
    while current < end:
        days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def _file_stat(path: Path) -> dict[str, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
