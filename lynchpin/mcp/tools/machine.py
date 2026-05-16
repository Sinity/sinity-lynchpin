"""Machine telemetry and machine-analysis MCP tools.

Do not enable postponed annotations in this module: FastMCP inspects function
annotations while registering @app.tool functions.
"""

import json
from pathlib import Path
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id, json_safe as _json_safe


def _analysis_artifact(name: str) -> dict[str, Any] | None:
    from lynchpin.analysis.core.io import resolve_analysis_path

    path = Path(resolve_analysis_path(name))
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else None


def _timestamp_filter(
    row: dict[str, Any],
    *,
    start: str | None,
    end: str | None,
    start_key: str,
    end_key: str,
) -> bool:
    if not start and not end:
        return True
    row_start = str(row.get(start_key) or "")
    row_end = str(row.get(end_key) or "")
    row_day_start = row_start[:10]
    row_day_end = row_end[:10] or row_day_start
    if start and row_day_end < start:
        return False
    if end and row_day_start > end:
        return False
    return True


@app.tool()
def machine_metrics_daily(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily machine telemetry rollup from the machine_metric_sample table."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "machine_metric_sample")
            if refresh_id is None:
                return []

        sql = """
            SELECT
                observed_at::DATE AS day,
                host,
                COUNT(*) AS samples,
                AVG(cpu_package_w) AS avg_cpu_package_w,
                MAX(cpu_package_w) AS max_cpu_package_w,
                AVG(gpu_power_w) AS avg_gpu_power_w,
                MAX(gpu_power_w) AS max_gpu_power_w,
                AVG(io_psi_some_avg10) AS avg_io_psi_some_avg10,
                MAX(io_psi_some_avg10) AS max_io_psi_some_avg10,
                AVG(latency_oversleep_ms) AS avg_latency_oversleep_ms,
                MAX(latency_oversleep_ms) AS max_latency_oversleep_ms,
                MAX(dstate_task_count) AS max_dstate_task_count
            FROM machine_metric_sample
            WHERE refresh_id = ?
        """
        params: list[Any] = [refresh_id]
        if start:
            sql += " AND observed_at::DATE >= ?"
            params.append(_date.fromisoformat(start))
        if end:
            sql += " AND observed_at::DATE <= ?"
            params.append(_date.fromisoformat(end))
        if host:
            sql += " AND host = ?"
            params.append(host)
        sql += " GROUP BY day, host ORDER BY day, host"

        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "date": _json_safe(row[0]),
            "host": row[1],
            "samples": row[2],
            "avg_cpu_package_w": row[3],
            "max_cpu_package_w": row[4],
            "avg_gpu_power_w": row[5],
            "max_gpu_power_w": row[6],
            "avg_io_psi_some_avg10": row[7],
            "max_io_psi_some_avg10": row[8],
            "avg_latency_oversleep_ms": row[9],
            "max_latency_oversleep_ms": row[10],
            "max_dstate_task_count": row[11],
        }
        for row in rows
    ]


@app.tool()
def machine_episodes(
    start: str | None = None,
    end: str | None = None,
    kind: str | None = None,
    host: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read typed machine episodes from the materialized analysis artifact."""
    payload = _analysis_artifact("machine_episode_analysis.json")
    if payload is None:
        return []
    episodes = [row for row in payload.get("episodes", []) if isinstance(row, dict)]
    rows = [
        row
        for row in episodes
        if (kind is None or row.get("kind") == kind)
        and (host is None or row.get("host") == host)
        and _timestamp_filter(row, start=start, end=end, start_key="started_at", end_key="ended_at")
    ]
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("kind") or ""), str(row.get("host") or "")))
    return rows[:max(limit, 0)]


@app.tool()
def machine_context_windows(
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    source: str | None = None,
    has_episodes: bool | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read machine/work context windows from the materialized artifact."""
    payload = _analysis_artifact("machine_context_windows.json")
    if payload is None:
        return []
    windows = [row for row in payload.get("windows", []) if isinstance(row, dict)]
    rows = []
    for row in windows:
        projects = row.get("projects") if isinstance(row.get("projects"), list) else []
        episode_count = int(row.get("episode_count") or 0)
        if project is not None and project not in projects:
            continue
        if source is not None and row.get("source") != source:
            continue
        if has_episodes is not None and bool(episode_count) is not has_episodes:
            continue
        if not _timestamp_filter(row, start=start, end=end, start_key="started_at", end_key="ended_at"):
            continue
        rows.append(row)
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("source") or ""), str(row.get("window_id") or "")))
    return rows[:max(limit, 0)]


@app.tool()
def machine_below_attributions(
    start: str | None = None,
    end: str | None = None,
    episode_kind: str | None = None,
    capture_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read below process/cgroup attribution rows for machine episodes."""
    payload = _analysis_artifact("machine_below_attribution.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "attributions": []}
    attributions = [row for row in payload.get("attributions", []) if isinstance(row, dict)]
    rows = [
        row
        for row in attributions
        if (episode_kind is None or row.get("episode_kind") == episode_kind)
        and (capture_id is None or row.get("capture_id") == capture_id)
        and _timestamp_filter(row, start=start, end=end, start_key="episode_started_at", end_key="episode_ended_at")
    ]
    rows.sort(key=lambda row: (-float(row.get("overlap_seconds") or 0), -float(row.get("severity") or 0), str(row.get("episode_started_at") or "")))
    summary = {
        "episode_count": payload.get("episode_count"),
        "attributed_episode_count": payload.get("attributed_episode_count"),
        "pressure_episode_count": payload.get("pressure_episode_count"),
        "unattributed_pressure_episode_count": payload.get("unattributed_pressure_episode_count"),
        "capture_count": payload.get("capture_count"),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "attributions": rows[:max(limit, 0)]}


@app.tool()
def machine_observational_baselines(
    dimension: str | None = None,
    key: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read observational machine telemetry baselines."""
    payload = _analysis_artifact("machine_observational_baselines.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "rows": []}
    families = {
        "hour": payload.get("by_hour", []),
        "source": payload.get("by_source", []),
        "hardware": payload.get("by_hardware_regime", []),
        "work_context": payload.get("work_context", []),
        "daily_signal": payload.get("daily_signals", []),
        "era_comparison": payload.get("era_comparisons", []),
    }
    selected = [dimension] if dimension else list(families)
    rows: list[dict[str, Any]] = []
    for family in selected:
        for row in families.get(family, []):
            if not isinstance(row, dict):
                continue
            row_key = row.get("key") or row.get("metric") or row.get("boundary")
            if key is not None and row_key != key:
                continue
            rows.append({"dimension": family, **row})
    summary = {
        "generated_for": payload.get("generated_for", {}),
        "caveats": payload.get("caveats", []),
        "family_counts": {name: len(rows) if isinstance(rows, list) else 0 for name, rows in families.items()},
    }
    return {"summary": summary, "rows": rows[:max(limit, 0)]}


@app.tool()
def machine_experiment_claims(
    claim_mode: str | None = None,
    workload: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read manifest-backed machine experiment claim packs."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "claim_packs": []}
    packs = [row for row in payload.get("claim_packs", []) if isinstance(row, dict)]
    rows = [
        row
        for row in packs
        if (claim_mode is None or row.get("claim_mode") == claim_mode)
        and (workload is None or row.get("workload") == workload)
    ]
    rows.sort(key=lambda row: (str(row.get("started_at") or ""), str(row.get("run_id") or "")))
    summary = {
        "run_count": payload.get("run_count"),
        "controlled_claim_count": payload.get("controlled_claim_count"),
        "observational_claim_count": payload.get("observational_claim_count"),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "claim_packs": rows[:max(limit, 0)]}


@app.tool()
def machine_service_state_summary(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    unit: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Summarize sampled systemd/user-unit state from machine_service_state."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "machine_service_state")
            if refresh_id is None:
                return []

        sql = """
            SELECT
                host,
                unit,
                scope,
                COUNT(*) AS samples,
                SUM(CASE WHEN active_state = 'active' THEN 1 ELSE 0 END) AS active_samples,
                MAX(memory_current_bytes) AS max_memory_current_bytes,
                MAX(cpu_usage_nsec) AS max_cpu_usage_nsec,
                MAX(io_read_bytes) AS max_io_read_bytes,
                MAX(io_write_bytes) AS max_io_write_bytes,
                MIN(observed_at) AS first_observed_at,
                MAX(observed_at) AS last_observed_at
            FROM machine_service_state
            WHERE refresh_id = ?
        """
        params: list[Any] = [refresh_id]
        if start:
            sql += " AND observed_at::DATE >= ?"
            params.append(_date.fromisoformat(start))
        if end:
            sql += " AND observed_at::DATE <= ?"
            params.append(_date.fromisoformat(end))
        if host:
            sql += " AND host = ?"
            params.append(host)
        if unit:
            sql += " AND unit = ?"
            params.append(unit)
        sql += " GROUP BY host, unit, scope ORDER BY host, scope, unit"

        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "host": row[0],
            "unit": row[1],
            "scope": row[2],
            "samples": row[3],
            "active_samples": row[4],
            "max_memory_current_bytes": row[5],
            "max_cpu_usage_nsec": row[6],
            "max_io_read_bytes": row[7],
            "max_io_write_bytes": row[8],
            "first_observed_at": _json_safe(row[9]),
            "last_observed_at": _json_safe(row[10]),
        }
        for row in rows
    ]
