"""Machine telemetry and machine-analysis MCP tools.

Do not enable postponed annotations in this module: FastMCP inspects function
annotations while registering @app.tool functions.
"""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from lynchpin.analysis.machine.status import machine_status_payload
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,
    ensure_substrate_materialized_for_read,
    json_safe as _json_safe,
)


def _ensure_machine_materialized_for_read(
    *,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    """Ensure canonical machine telemetry before reading promoted tables."""

    from lynchpin.materialization import ensure_materialized

    window = (start, end) if start is not None and end is not None else None
    return ensure_materialized("machine", window=window).to_json()


def _exclusive_end(end: Any) -> Any:
    return end + timedelta(days=1) if end is not None else None


def _ensure_work_observation_substrate_for_read(
    *,
    caller: str,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    window = (start, end) if start is not None and end is not None else None
    return ensure_substrate_materialized_for_read(caller=caller, window=window)


def _analysis_artifact(name: str) -> dict[str, Any] | None:
    from lynchpin.core.io import load_materialized_analysis_artifact

    payload, _materialization = load_materialized_analysis_artifact(name)
    return payload if isinstance(payload, dict) else None


def _required_analysis_artifact(name: str) -> dict[str, Any]:
    from lynchpin.core.io import resolve_analysis_path

    path = Path(resolve_analysis_path(name))
    payload = _analysis_artifact(name)
    if payload is None:
        raise FileNotFoundError(
            f"required machine analysis artifact is missing or malformed: {path}"
        )
    return payload


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


def _artifact_rows(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if payload is None:
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _workflow_mechanics_artifact_payload(
    *,
    start: str | None,
    end: str | None,
    project: str | None,
    refresh_id: str | None,
    retry_gap_min: int,
    limit: int,
) -> dict[str, Any] | None:
    if (
        start is not None
        or end is not None
        or project is not None
        or refresh_id is not None
        or retry_gap_min != 20
        or limit != 100
    ):
        return None

    payload = _analysis_artifact("workflow_mechanics.json")
    if payload is None:
        return None
    if payload.get("start") is not None or payload.get("end") is not None:
        return None
    return _json_safe({**payload, "source": "artifact"})


@app.tool()
def machine_status() -> dict[str, Any]:
    """Summarize current machine-analysis readiness, support, claims, and blockers."""
    return _json_safe(machine_status_payload())


@app.tool()
def machine_service_io_for_xtask_invocation(
    invocation_id: int,
    limit: int = 20,
    min_total_mib: float = 0.0,
    include_below_processes: bool = False,
    below_top_per_sample: int = 20,
) -> dict[str, Any]:
    """Attribute machine I/O counters for one exact Sinex xtask invocation.

    This reads the Sinex xtask history DB and Sinnix machine telemetry SQLite
    through Lynchpin source APIs. It intentionally does not require DuckDB
    substrate promotion because the high-rate block/cgroup attribution tables
    are already canonical in the machine telemetry SQLite.
    """
    from lynchpin.analysis.machine.service_io import (
        analyze_machine_service_io_for_xtask_invocation,
    )

    report = analyze_machine_service_io_for_xtask_invocation(
        invocation_id,
        limit=limit,
        min_total_mib=min_total_mib,
        include_below_processes=include_below_processes,
        below_top_per_sample=below_top_per_sample,
    )
    return _json_safe(
        {
            **report.to_dict(),
            "source_mode": "direct_live_sources",
            "source_databases": (
                "sinex xtask history SQLite",
                "sinnix machine telemetry SQLite",
            ),
            "substrate_promotion_required": False,
        }
    )


@app.tool()
def machine_xtask_contention(
    start: str | None = None,
    end: str | None = None,
    hours: float = 24.0,
    command: str | None = None,
    limit: int = 10,
    min_duration_s: float = 30.0,
    min_io_full_max: float = 0.0,
    success_only: bool = False,
    include_below_processes: bool = False,
    below_top_per_sample: int = 12,
) -> dict[str, Any]:
    """Rank slow xtask invocations and attribute their machine I/O windows.

    Uses direct source reads for xtask history and machine telemetry. DuckDB is
    not in the read path for this exact-window report.
    """
    from datetime import datetime, timezone

    from lynchpin.analysis.machine.xtask_contention import analyze_xtask_contention

    end_dt = (
        datetime.fromisoformat(end.replace("Z", "+00:00"))
        if end is not None
        else datetime.now(timezone.utc)
    )
    start_dt = (
        datetime.fromisoformat(start.replace("Z", "+00:00"))
        if start is not None
        else end_dt - timedelta(hours=hours)
    )
    report = analyze_xtask_contention(
        start=start_dt,
        end=end_dt,
        command=command,
        limit=limit,
        min_duration_s=min_duration_s,
        min_io_full_max=min_io_full_max,
        include_failures=not success_only,
        include_below_processes=include_below_processes,
        below_top_per_sample=below_top_per_sample,
    )
    return _json_safe(
        {
            **report.to_dict(),
            "source_mode": "direct_live_sources",
            "source_databases": (
                "sinex xtask history SQLite",
                "sinnix machine telemetry SQLite",
            ),
            "substrate_promotion_required": False,
        }
    )


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
    from lynchpin.substrate.machine import load_machine_metric_daily

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        _ensure_machine_materialized_for_read(start=start_d, end=materialization_end)
        ensure_substrate_materialized_for_read(
            caller="machine_metrics_daily",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "machine_metric_sample", caller="machine_metrics_daily")
            if refresh_id is None:
                return []

        rows = load_machine_metric_daily(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            host=host,
        )

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
def machine_metrics_by_context(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily machine-metric series segmented by the Layer-1 context vector.

    Splits each day by software_revision (the NixOS generation active at each
    sample, via an ASOF join on activated_at <= observed_at) and hardware_regime
    (GPU PCIe link gen/width), aggregating CPU/GPU power and CPU/IO pressure.
    Use this to compare a metric across generation boundaries or PCIe link
    regimes. ``generation`` is null for samples predating any activation record
    (missing generation telemetry is reported as null, never imputed).
    """
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_machine_metric_series_by_context

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        _ensure_machine_materialized_for_read(start=start_d, end=materialization_end)
        ensure_substrate_materialized_for_read(
            caller="machine_metrics_by_context",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(
                conn,
                "machine_metric_sample",
                caller="machine_metrics_by_context",
            )
            if refresh_id is None:
                return []
        generations_refresh_id = best_materialized_refresh_id(
            conn,
            "sinnix_generation",
            caller="machine_metrics_by_context.generations",
        )

        rows = load_machine_metric_series_by_context(
            conn,
            refresh_id=refresh_id,
            generations_refresh_id=generations_refresh_id,
            start=start_d,
            end=end_d,
            host=host,
        )

    return [
        {
            "day": _json_safe(row[0]),
            "generation": row[1],
            "sinnix_revision": row[2],
            "gpu_pcie_gen": row[3],
            "gpu_pcie_width": row[4],
            "samples": row[5],
            "avg_cpu_package_w": row[6],
            "avg_gpu_power_w": row[7],
            "avg_io_psi_full_avg10": row[8],
            "max_io_psi_full_avg10": row[9],
            "avg_cpu_psi_some_avg60": row[10],
        }
        for row in rows
    ]


@app.tool()
def machine_dataset_inventory(project: str | None = None, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Read machine-analysis table and artifact inventory from readiness output."""
    del project
    payload = _analysis_artifact("machine_analysis_readiness.json")
    if payload is None:
        return {"summary": {"status": "missing", "filters": {"start": start, "end": end}}, "tables": [], "artifacts": []}
    tables = payload.get("tables", []) if isinstance(payload.get("tables"), list) else []
    artifacts = payload.get("artifacts", []) if isinstance(payload.get("artifacts"), list) else []
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "filters": {"start": start, "end": end},
            "table_count": len(tables),
            "artifact_count": len(artifacts),
            "caveats": payload.get("caveats", []),
        },
        "tables": tables,
        "artifacts": artifacts,
    }


def _machine_materialization_health_payload() -> dict[str, Any]:
    payload = _analysis_artifact("machine_analysis_readiness.json")
    report = _analysis_artifact("machine_analysis_materialization_report.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "dimensions": [], "latest_materialization_report": report}
    dimensions = [row for row in payload.get("dimensions", []) if isinstance(row, dict)]
    status_counts: dict[str, int] = {}
    for row in dimensions:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    health = "stable" if dimensions and set(status_counts) <= {"stable"} else "degraded"
    return {
        "summary": {
            "status": health,
            "generated_at_utc": payload.get("generated_at_utc"),
            "dimension_count": len(dimensions),
            "by_status": dict(sorted(status_counts.items())),
            "caveats": payload.get("caveats", []),
        },
        "dimensions": dimensions,
        "latest_materialization_report": report,
    }


@app.tool()
def machine_materialization_health() -> dict[str, Any]:
    """Summarize machine-analysis materialization health from readiness dimensions."""
    return _machine_materialization_health_payload()


@app.tool()
def machine_calibration_fixtures(kind: str | None = None, status: str | None = None) -> dict[str, Any]:
    """Read deterministic calibration fixtures for causal-infra guardrails."""
    payload = _analysis_artifact("machine_calibration_fixtures.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "fixtures": []}
    rows = [row for row in payload.get("fixtures", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("fixture_kind") == kind]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "fixture_count": payload.get("fixture_count", len(rows)),
            "by_status": payload.get("by_status", {}),
            "caveats": payload.get("caveats", []),
        },
        "fixtures": rows,
    }


@app.tool()
def machine_measurement_system(kind: str | None = None, status: str | None = None) -> dict[str, Any]:
    """Read measurement-system diagnostics for machine causal analysis."""
    payload = _analysis_artifact("machine_measurement_system.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "checks": []}
    rows = [row for row in payload.get("checks", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("check_kind") == kind]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "check_count": payload.get("check_count", len(rows)),
            "by_status": payload.get("by_status", {}),
            "caveats": payload.get("caveats", []),
        },
        "checks": rows,
    }


@app.tool()
def machine_episodes(
    start: str | None = None,
    end: str | None = None,
    kind: str | None = None,
    host: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read typed machine episodes from the materialized analysis artifact."""
    from lynchpin.analysis.machine.episodes import EPISODE_DETECTOR_VERSION

    payload = _required_analysis_artifact("machine_episode_analysis.json")
    if payload.get("detector_version") != EPISODE_DETECTOR_VERSION:
        raise RuntimeError(
            "machine_episode_analysis.json was generated by an obsolete episode detector; "
            "run `python -m lynchpin.analysis machine-episodes` or `just analysis-materialize`"
        )
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
    payload = _required_analysis_artifact("machine_context_windows.json")
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
    attribution_source: str = "below",
    limit: int = 100,
) -> dict[str, Any]:
    """Read bounded-below or workload-resource attribution rows for machine episodes."""
    payload = _analysis_artifact("machine_below_attribution.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "attributions": []}
    row_key = "workload_resource_attributions" if attribution_source == "workload_resource" else "attributions"
    attributions = [row for row in payload.get(row_key, []) if isinstance(row, dict)]
    rows = [
        row
        for row in attributions
        if (episode_kind is None or row.get("episode_kind") == episode_kind)
        and (capture_id is None or row.get("capture_id") == capture_id or row.get("work_source_id") == capture_id)
        and _timestamp_filter(row, start=start, end=end, start_key="episode_started_at", end_key="episode_ended_at")
    ]
    rows.sort(key=lambda row: (-float(row.get("overlap_seconds") or 0), -float(row.get("severity") or 0), str(row.get("episode_started_at") or "")))
    summary = {
        "episode_count": payload.get("episode_count"),
        "attributed_episode_count": payload.get("attributed_episode_count"),
        "pressure_episode_count": payload.get("pressure_episode_count"),
        "unattributed_pressure_episode_count": payload.get("unattributed_pressure_episode_count"),
        "workload_resource_attributed_pressure_episode_count": payload.get("workload_resource_attributed_pressure_episode_count"),
        "residual_unattributed_pressure_episode_count": payload.get("residual_unattributed_pressure_episode_count"),
        "capture_count": payload.get("capture_count"),
        "attribution_source": attribution_source,
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "attributions": rows[:max(limit, 0)]}


@app.tool()
def machine_telemetry_analysis(section: str = "daily", limit: int = 100) -> dict[str, Any]:
    """Read the materialized machine telemetry analysis artifact."""
    payload = _analysis_artifact("machine_telemetry_analysis.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "rows": []}
    valid_sections = {"daily", "signals", "hardware_regimes", "correlations"}
    selected = section if section in valid_sections else "daily"
    rows = _artifact_rows(payload, selected)
    summary = {
        "generated_at_utc": payload.get("generated_at_utc"),
        "coverage": payload.get("coverage", {}),
        "section": selected,
        "row_count": len(rows),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "rows": rows[:max(limit, 0)]}


@app.tool()
def machine_below_analysis(section: str = "system", capture_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Read materialized below system/process/cgroup summaries."""
    payload = _analysis_artifact("machine_below_analysis.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "rows": []}
    section_map = {"system": "system", "processes": "top_processes", "cgroups": "top_cgroups"}
    selected = section_map.get(section, "system")
    rows = _artifact_rows(payload, selected)
    if capture_id is not None:
        rows = [row for row in rows if row.get("capture_id") == capture_id]
    summary = {
        "generated_at_utc": payload.get("generated_at_utc"),
        "window_count": payload.get("window_count"),
        "live_store": payload.get("live_store", {}),
        "section": section,
        "row_count": len(rows),
        "top_process_count": payload.get("top_process_count"),
        "top_cgroup_count": payload.get("top_cgroup_count"),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "rows": rows[:max(limit, 0)]}


@app.tool()
def machine_work_state_windows(
    pressure_state: str | None = None,
    work_state: str | None = None,
    project: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read segmented machine/work state windows from the materialized artifact."""
    payload = _analysis_artifact("machine_work_state_windows.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "windows": []}
    rows = _artifact_rows(payload, "windows")
    if pressure_state is not None:
        rows = [row for row in rows if row.get("pressure_state") == pressure_state]
    if work_state is not None:
        rows = [row for row in rows if row.get("work_state") == work_state]
    if project is not None:
        rows = [
            row for row in rows
            if project in (row.get("projects") if isinstance(row.get("projects"), list) else [])
        ]
    summary = {
        "generated_at_utc": payload.get("generated_at_utc"),
        "generated_for": payload.get("generated_for", {}),
        "window_count": payload.get("window_count", len(rows)),
        "pressure_state_counts": payload.get("pressure_state_counts", {}),
        "work_state_counts": payload.get("work_state_counts", {}),
        "hardware_regime_counts": payload.get("hardware_regime_counts", {}),
        "repo_state_counts": payload.get("repo_state_counts", {}),
        "filters": {"pressure_state": pressure_state, "work_state": work_state, "project": project},
        "filtered_count": len(rows),
        "caveats": payload.get("caveats", []),
    }
    rows.sort(key=lambda row: str(row.get("started_at") or ""))
    return {"summary": summary, "windows": rows[:max(limit, 0)]}


@app.tool()
def machine_command_performance(
    tool: str | None = None,
    project: str | None = None,
    pressure_only: bool = False,
    refresh_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read command-performance windows joined to machine work states."""
    promoted_only_tool = tool in ("xtask", "polylogue")
    payload = None if promoted_only_tool else _analysis_artifact("command_performance_windows.json")
    rows = _artifact_rows(payload, "windows")
    if payload is not None:
        if tool is not None:
            rows = [row for row in rows if row.get("tool") == tool]
        if project is not None:
            rows = [row for row in rows if row.get("project") == project]
        if pressure_only:
            rows = [row for row in rows if row.get("machine_pressure_state") not in (None, "", "quiet")]
    work_rows, work_caveats = _work_observation_command_windows(
        tool=tool,
        project=project,
        pressure_only=pressure_only,
        refresh_id=refresh_id,
    )
    rows.extend(work_rows)
    if payload is None and not rows:
        return {"summary": {"status": "missing"}, "windows": []}
    rows.sort(key=lambda row: float(row.get("duration_seconds") or 0), reverse=True)
    tool_summaries = _artifact_rows(payload, "tools")
    if tool is not None:
        tool_summaries = [row for row in tool_summaries if row.get("tool") == tool]
    if work_rows:
        tool_summaries = _merge_command_tool_summaries(tool_summaries, _tool_summaries_from_windows(work_rows))
    summary = {
        "generated_at_utc": payload.get("generated_at_utc") if payload else None,
        "generated_for": payload.get("generated_for", {}) if payload else {},
        "command_count": (payload.get("command_count", 0) if payload else 0) + len(work_rows),
        "tool_count": len(tool_summaries),
        "filters": {
            "tool": tool,
            "project": project,
            "pressure_only": pressure_only,
            "refresh_id": refresh_id,
        },
        "filtered_count": len(rows),
        "tool_summaries": tool_summaries,
        "caveats": sorted(dict.fromkeys([
            *(payload.get("caveats", []) if payload else []),
            *work_caveats,
            *(
                [
                    "xtask rows come from promoted work_observation ledgers, not shell history",
                    "xtask pressure state is derived from host PSI maxima recorded by xtask",
                ]
                if any(row.get("tool") == "xtask" for row in work_rows)
                else []
            ),
            *(
                [
                    "polylogue devtools rows come from promoted work_observation ledgers, not shell history",
                ]
                if any(row.get("tool") == "polylogue" for row in work_rows)
                else []
            ),
        ])),
    }
    return {"summary": summary, "windows": rows[:max(limit, 0)]}


@app.tool()
def machine_observational_deltas(
    tool: str | None = None,
    work_state: str | None = None,
    pressure_state: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read observational command-duration deltas by work/pressure cohort."""
    payload = _analysis_artifact("machine_observational_deltas.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "deltas": [], "cohorts": []}
    deltas = _artifact_rows(payload, "deltas")
    cohorts = _artifact_rows(payload, "cohorts")

    def keep(row: dict[str, Any]) -> bool:
        return (
            (tool is None or row.get("tool") == tool)
            and (work_state is None or row.get("work_state") == work_state)
            and (pressure_state is None or row.get("pressure_state") == pressure_state)
        )

    deltas = [row for row in deltas if keep(row)]
    cohorts = [row for row in cohorts if keep(row)]
    deltas.sort(key=lambda row: float(row.get("median_delta_seconds") or 0), reverse=True)
    summary = {
        "generated_at_utc": payload.get("generated_at_utc"),
        "generated_for": payload.get("generated_for", {}),
        "cohort_count": payload.get("cohort_count", len(cohorts)),
        "delta_count": payload.get("delta_count", len(deltas)),
        "filters": {"tool": tool, "work_state": work_state, "pressure_state": pressure_state},
        "filtered_delta_count": len(deltas),
        "filtered_cohort_count": len(cohorts),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "deltas": deltas[:max(limit, 0)], "cohorts": cohorts[:max(limit, 0)]}


@app.tool()
def machine_devshell_performance(
    command_class: str | None = None,
    pressure_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Read devshell/Nix command performance windows."""
    payload = _analysis_artifact("devshell_performance.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "windows": []}
    rows = _artifact_rows(payload, "windows")
    if command_class is not None:
        rows = [row for row in rows if row.get("command_class") == command_class]
    if pressure_only:
        rows = [row for row in rows if row.get("machine_pressure_state") not in (None, "", "quiet")]
    rows.sort(key=lambda row: float(row.get("duration_seconds") or 0), reverse=True)
    summary = {
        "generated_at_utc": payload.get("generated_at_utc"),
        "generated_for": payload.get("generated_for", {}),
        "command_count": payload.get("command_count", len(rows)),
        "summary_count": len(_artifact_rows(payload, "summaries")),
        "filters": {"command_class": command_class, "pressure_only": pressure_only},
        "filtered_count": len(rows),
        "summaries": _artifact_rows(payload, "summaries"),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "windows": rows[:max(limit, 0)]}


def _work_observation_command_windows(
    *,
    tool: str | None,
    project: str | None,
    pressure_only: bool,
    refresh_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if tool not in (None, "xtask", "polylogue"):
        return [], []

    from lynchpin.substrate.connection import connect, substrate_path

    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(caller="machine_command_performance")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_or_none(conn, "work_observation")
        if refresh_id is None:
            return [], []
        stage_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM work_observation_stage WHERE refresh_id = ?",
                [refresh_id],
            ).fetchone()[0]
            or 0
        )
        test_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM work_observation_test_result WHERE refresh_id = ?",
                [refresh_id],
            ).fetchone()[0]
            or 0
        )
        clauses = ["refresh_id = ?"]
        params: list[Any] = [refresh_id]
        source_predicates = []
        if tool in (None, "xtask"):
            source_predicates.append("(source = 'xtask_history' OR work_kind = 'xtask_invocation')")
        if tool in (None, "polylogue"):
            source_predicates.append("(source = 'polylogue_devtools' OR work_kind IN ('polylogue_devtools_invocation', 'polylogue_log_run'))")
        clauses.append("(" + " OR ".join(source_predicates) + ")")
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if pressure_only:
            clauses.append(
                """
                (
                  COALESCE(host_io_pressure_some_avg10_max, 0) >= 10
                  OR COALESCE(host_memory_pressure_some_avg10_max, 0) >= 10
                  OR COALESCE(host_cpu_pressure_some_avg10_max, 0) >= 10
                )
                """
            )
        rows = conn.execute(
            f"""
            SELECT
              source_id,
              source,
              work_kind,
              started_at,
              ended_at,
              duration_s,
              exit_code,
              cwd,
              project,
              command,
              status,
              host,
              host_cpu_pressure_some_avg10_max,
              host_io_pressure_some_avg10_max,
              host_io_pressure_full_avg10_max,
              host_memory_pressure_some_avg10_max,
              host_memory_pressure_full_avg10_max,
              process_count_max,
              shm_free_min_mb,
              shm_used_max_mb
            FROM work_observation
            WHERE {" AND ".join(clauses)}
            """,
            params,
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        pressure_state = _work_observation_pressure_state(
            cpu_some=row[12],
            io_some=row[13],
            io_full=row[14],
            memory_some=row[15],
            memory_full=row[16],
        )
        row_tool = _work_observation_tool(row[1], row[2])
        result.append(
            {
                "source": "work_observation",
                "source_id": row[0],
                "tool": row_tool,
                "project": row[8],
                "command": list(row[9] or ()),
                "command_prefix": " ".join(list(row[9] or ())[:2]),
                "duration_seconds": _round(row[5]),
                "exit_code": row[6],
                "cwd": row[7],
                "started_at": _json_safe(row[3]),
                "ended_at": _json_safe(row[4]),
                "status": row[10],
                "host": row[11],
                "machine_pressure_state": pressure_state,
                "machine_work_state": "test_workload"
                if row_tool == "xtask" and any(part in {"test", "ci", "nextest"} for part in (row[9] or ()))
                else "devtools_workload" if row_tool == "polylogue" else "build_workload",
                "machine_overlap_seconds": _round(row[5]),
                "host_cpu_pressure_some_avg10_max": _round(row[12]),
                "host_io_pressure_some_avg10_max": _round(row[13]),
                "host_io_pressure_full_avg10_max": _round(row[14]),
                "host_memory_pressure_some_avg10_max": _round(row[15]),
                "host_memory_pressure_full_avg10_max": _round(row[16]),
                "process_count_max": row[17],
                "shm_free_min_mb": _round(row[18]),
                "shm_used_max_mb": _round(row[19]),
            }
        )
    caveats = []
    if tool in (None, "xtask") and not any(row.get("tool") == "xtask" for row in result) and (stage_count or test_count):
        caveats.append(
            "xtask stage/test ledgers are present but xtask invocation rows are missing from work_observation; rerun the work-observation promotion"
        )
    if any(row.get("tool") == "polylogue" for row in result):
        caveats.append("polylogue devtools rows come from promoted work_observation ledgers")
    return result, caveats


def _work_observation_tool(source: Any, work_kind: Any) -> str:
    if source == "polylogue_devtools" or work_kind in {"polylogue_devtools_invocation", "polylogue_log_run"}:
        return "polylogue"
    if source == "xtask_history" or work_kind == "xtask_invocation":
        return "xtask"
    return "work_observation"


def _work_observation_pressure_state(
    *,
    cpu_some: Any,
    io_some: Any,
    io_full: Any,
    memory_some: Any,
    memory_full: Any,
) -> str:
    if _float_or_zero(io_full) >= 10 or _float_or_zero(io_some) >= 10:
        return "io_pressure"
    if _float_or_zero(memory_full) >= 10 or _float_or_zero(memory_some) >= 10:
        return "memory_pressure"
    if _float_or_zero(cpu_some) >= 10:
        return "cpu_pressure"
    return "quiet"


def _tool_summaries_from_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_tool.setdefault(str(row.get("tool") or "unknown"), []).append(row)
    summaries: list[dict[str, Any]] = []
    for tool, tool_rows in by_tool.items():
        durations = sorted(
            float(row["duration_seconds"])
            for row in tool_rows
            if row.get("duration_seconds") is not None
        )
        summaries.append(
            {
                "tool": tool,
                "command_count": len(tool_rows),
                "error_count": sum(1 for row in tool_rows if row.get("exit_code") not in (None, 0)),
                "median_duration_seconds": _median(durations),
                "p95_duration_seconds": _p95(durations),
                "pressure_overlap_count": sum(
                    1
                    for row in tool_rows
                    if row.get("machine_pressure_state") not in (None, "", "quiet")
                ),
            }
        )
    return sorted(summaries, key=lambda row: (-int(row["command_count"]), str(row["tool"])))


def _merge_command_tool_summaries(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {str(row.get("tool") or "unknown"): dict(row) for row in left}
    for row in right:
        tool = str(row.get("tool") or "unknown")
        if tool not in merged:
            merged[tool] = dict(row)
            continue
        current = merged[tool]
        current["command_count"] = int(current.get("command_count") or 0) + int(row.get("command_count") or 0)
        current["error_count"] = int(current.get("error_count") or 0) + int(row.get("error_count") or 0)
        current["pressure_overlap_count"] = int(current.get("pressure_overlap_count") or 0) + int(row.get("pressure_overlap_count") or 0)
    return sorted(merged.values(), key=lambda row: (-int(row.get("command_count") or 0), str(row.get("tool") or "")))


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
        "by_manifest_validation_status": _count_manifest_validation_status(rows),
        "caveats": payload.get("caveats", []),
    }
    return {"summary": summary, "claim_packs": rows[:max(limit, 0)]}


@app.tool()
def machine_benchmark_runs(
    limit: int = 100,
    run_group_id: str | None = None,
    workload: str | None = None,
) -> dict[str, Any]:
    """Read manifest-backed benchmark/experiment run claim packs."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "runs": []}
    rows = [row for row in payload.get("claim_packs", []) if isinstance(row, dict)]
    if run_group_id:
        rows = [row for row in rows if row.get("run_group_id") == run_group_id]
    if workload:
        rows = [row for row in rows if row.get("workload") == workload]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "run_count": payload.get("run_count", len(rows)),
            "controlled_claim_count": payload.get("controlled_claim_count"),
            "observational_claim_count": payload.get("observational_claim_count"),
            "by_manifest_validation_status": _count_manifest_validation_status(rows),
            "caveats": payload.get("caveats", []),
        },
        "runs": rows[:max(limit, 0)],
    }


@app.tool()
def machine_benchmark_phases(
    limit: int = 200,
    run_id: str | None = None,
    derivation: str | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    """Read parsed Nix internal-json phases embedded in benchmark run packs."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "phases": []}
    rows = []
    for pack in payload.get("claim_packs", []):
        if not isinstance(pack, dict):
            continue
        if run_id and pack.get("run_id") != run_id:
            continue
        internal_json = pack.get("internal_json") if isinstance(pack.get("internal_json"), dict) else {}
        for row in internal_json.get("phases", []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("activity_type") or "")
            if phase and phase not in name:
                continue
            if derivation and derivation not in name:
                continue
            rows.append({"run_id": pack.get("run_id"), "run_group_id": pack.get("run_group_id"), **row})
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "phase_count": len(rows),
            "caveats": payload.get("caveats", []),
        },
        "phases": rows[:max(limit, 0)],
    }


@app.tool()
def machine_benchmark_estimates(run_group_id: str | None = None, metric: str | None = None) -> dict[str, Any]:
    """Read effect estimates, intervals, and randomization p-values for benchmark run groups."""
    payload = _analysis_artifact("machine_experiment_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "estimates": []}
    rows = [row for row in payload.get("effect_estimates", []) if isinstance(row, dict)]
    if run_group_id:
        rows = [row for row in rows if row.get("run_group_id") == run_group_id]
    if metric:
        rows = [row for row in rows if row.get("metric") == metric]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "estimate_count": len(rows),
            "controlled_claim_count": payload.get("controlled_claim_count"),
            "caveats": payload.get("caveats", []),
        },
        "estimates": rows,
    }


@app.tool()
def machine_work_observation_daily(
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    command_contains: str | None = None,
    refresh_id: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Daily xtask/work-observation rollups from promoted work_observation rows."""
    from datetime import date as _date

    from lynchpin.analysis.machine.work_observations import daily_work_observation_series
    from lynchpin.substrate.connection import connect, substrate_path

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(
            caller="machine_work_observation_daily",
            start=start_d,
            end=end_d,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_or_none(conn, "work_observation")
            if refresh_id is None:
                return {"summary": {"status": "missing"}, "rows": []}
        rows = daily_work_observation_series(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            project=project,
            command_contains=command_contains,
        )

    return {
        "summary": {
            "refresh_id": refresh_id,
            "row_count": len(rows),
            "filters": {
                "start": start,
                "end": end,
                "project": project,
                "command_contains": command_contains,
            },
        },
        "rows": [
            {
                "date": _json_safe(row.date),
                "work_kind": row.work_kind,
                "project": row.project,
                "command": list(row.command),
                "observation_count": row.observation_count,
                "success_count": row.success_count,
                "failed_count": row.failed_count,
                "avg_duration_s": _round(row.avg_duration_s),
                "median_duration_s": _round(row.median_duration_s),
                "p95_duration_s": _round(row.p95_duration_s),
                "max_duration_s": _round(row.max_duration_s),
            }
            for row in rows[:max(limit, 0)]
        ],
    }


@app.tool()
def machine_workflow_mechanics(
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    refresh_id: str | None = None,
    retry_gap_min: int = 20,
    limit: int = 100,
) -> dict[str, Any]:
    """Workflow mechanics over work observations: command summaries and retry loops."""
    from datetime import date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.analysis.workflow_mechanics import analyze_workflow_mechanics

    artifact_payload = _workflow_mechanics_artifact_payload(
        start=start,
        end=end,
        project=project,
        refresh_id=refresh_id,
        retry_gap_min=retry_gap_min,
        limit=limit,
    )
    if artifact_payload is not None:
        return artifact_payload

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(
            caller="machine_workflow_mechanics",
            start=start_d,
            end=end_d,
        )
        with connect(substrate_path(), read_only=True) as conn:
            refresh_id = _best_refresh_or_none(conn, "work_observation")

    payload = analyze_workflow_mechanics(
        start=start_d,
        end=end_d,
        project=project,
        refresh_id=refresh_id,
        retry_gap_min=retry_gap_min,
        limit=min(max(limit, 1), 500),
    ).to_json()
    payload["source"] = "live_analysis"
    return payload


@app.tool()
def machine_work_stage_summary(
    start: str | None = None,
    end: str | None = None,
    stage_name: str | None = None,
    refresh_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Slowest xtask stage summaries from promoted work_observation_stage rows."""
    from datetime import date as _date

    from lynchpin.analysis.machine.work_observations import stage_timing_summary
    from lynchpin.substrate.connection import connect, substrate_path

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(
            caller="machine_work_stage_summary",
            start=start_d,
            end=end_d,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_or_none(conn, "work_observation_stage")
            if refresh_id is None:
                return {"summary": {"status": "missing"}, "rows": []}
        rows = stage_timing_summary(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            stage_name=stage_name,
            limit=limit,
        )

    return {
        "summary": {
            "refresh_id": refresh_id,
            "row_count": len(rows),
            "filters": {"start": start, "end": end, "stage_name": stage_name},
        },
        "rows": [
            {
                "stage_name": row.stage_name,
                "observation_count": row.observation_count,
                "success_count": row.success_count,
                "avg_duration_s": _round(row.avg_duration_s),
                "median_duration_s": _round(row.median_duration_s),
                "p95_duration_s": _round(row.p95_duration_s),
                "max_duration_s": _round(row.max_duration_s),
            }
            for row in rows
        ],
    }


@app.tool()
def machine_work_test_summary(
    package: str | None = None,
    refresh_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Slowest xtask test summaries from promoted work_observation_test_result rows."""
    from lynchpin.analysis.machine.work_observations import test_duration_summary
    from lynchpin.substrate.connection import connect, substrate_path

    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(caller="machine_work_test_summary")

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_or_none(conn, "work_observation_test_result")
            if refresh_id is None:
                return {"summary": {"status": "missing"}, "rows": []}
        rows = test_duration_summary(
            conn,
            refresh_id=refresh_id,
            package=package,
            limit=limit,
        )

    return {
        "summary": {
            "refresh_id": refresh_id,
            "row_count": len(rows),
            "filters": {"package": package},
            "caveats": [
                "xtask test rows are child observations and do not carry independent timestamps",
            ],
        },
        "rows": [
            {
                "package": row.package,
                "status": row.status,
                "test_count": row.test_count,
                "avg_duration_s": _round(row.avg_duration_s),
                "median_duration_s": _round(row.median_duration_s),
                "p95_duration_s": _round(row.p95_duration_s),
                "max_duration_s": _round(row.max_duration_s),
            }
            for row in rows
        ],
    }


@app.tool()
def machine_work_observation_artifact() -> dict[str, Any]:
    """Read the materialized machine_work_observations artifact summary."""
    payload = _analysis_artifact("machine_work_observations.json")
    if payload is None:
        return {
            "summary": {"status": "missing"},
            "daily": [],
            "stage_summaries": [],
            "test_summaries": [],
            "failure_summaries": [],
        }
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "window": payload.get("window"),
            "refresh_id": payload.get("refresh_id"),
            "daily_count": len(payload.get("daily", [])) if isinstance(payload.get("daily"), list) else 0,
            "stage_summary_count": len(payload.get("stage_summaries", []))
            if isinstance(payload.get("stage_summaries"), list)
            else 0,
            "test_summary_count": len(payload.get("test_summaries", []))
            if isinstance(payload.get("test_summaries"), list)
            else 0,
            "failure_summary_count": len(payload.get("failure_summaries", []))
            if isinstance(payload.get("failure_summaries"), list)
            else 0,
            "caveats": payload.get("caveats", []),
        },
        "daily": payload.get("daily", []),
        "sinex_check_daily": payload.get("sinex_check_daily", []),
        "stage_summaries": payload.get("stage_summaries", []),
        "test_summaries": payload.get("test_summaries", []),
        "failure_summaries": payload.get("failure_summaries", []),
    }


@app.tool()
def machine_work_slow_tests(
    package: str | None = None,
    project: str | None = None,
    limit: int = 100,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Read slow xtask test-result rows with invocation project context."""
    from lynchpin.substrate.connection import connect, substrate_path

    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(caller="machine_work_slow_tests")

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_or_none(conn, "work_observation_test_result")
            if refresh_id is None:
                return {"summary": {"status": "missing"}, "rows": []}
        clauses = ["t.refresh_id = ?"]
        params: list[Any] = [refresh_id]
        if package is not None:
            clauses.append("t.package = ?")
            params.append(package)
        if project is not None:
            clauses.append("w.project = ?")
            params.append(project)
        params.append(max(limit, 0))
        rows = conn.execute(
            f"""
            SELECT t.package, t.test_name, t.status, t.duration_s, w.project, w.command
            FROM work_observation_test_result t
            LEFT JOIN work_observation w
              ON w.source_id = t.invocation_source_id AND w.refresh_id = t.refresh_id
            WHERE {" AND ".join(clauses)}
            ORDER BY t.duration_s DESC NULLS LAST, t.package, t.test_name
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {
        "summary": {"refresh_id": refresh_id, "row_count": len(rows), "filters": {"package": package, "project": project}},
        "rows": [
            {
                "package": row[0],
                "test_name": row[1],
                "status": row[2],
                "duration_s": _round(row[3]),
                "project": row[4],
                "command": list(row[5] or ()),
            }
            for row in rows
        ],
    }


@app.tool()
def machine_work_stage_daily(
    stage_name: str | None = None,
    project: str | None = None,
    limit: int = 500,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Read daily xtask stage timing grouped by stage and invocation project."""
    from lynchpin.substrate.connection import connect, substrate_path

    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(caller="machine_work_stage_daily")

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_or_none(conn, "work_observation_stage")
            if refresh_id is None:
                return {"summary": {"status": "missing"}, "rows": []}
        clauses = ["s.refresh_id = ?"]
        params: list[Any] = [refresh_id]
        if stage_name is not None:
            clauses.append("s.stage_name = ?")
            params.append(stage_name)
        if project is not None:
            clauses.append("w.project = ?")
            params.append(project)
        params.append(max(limit, 0))
        rows = conn.execute(
            f"""
            SELECT
              CAST(s.started_at AS DATE) AS date,
              s.stage_name,
              w.project,
              COUNT(*) AS observation_count,
              SUM(CASE WHEN s.success THEN 1 ELSE 0 END) AS success_count,
              MEDIAN(s.duration_s) AS median_duration_s,
              QUANTILE_CONT(s.duration_s, 0.95) AS p95_duration_s,
              MAX(s.duration_s) AS max_duration_s
            FROM work_observation_stage s
            LEFT JOIN work_observation w
              ON w.source_id = s.invocation_source_id AND w.refresh_id = s.refresh_id
            WHERE {" AND ".join(clauses)}
            GROUP BY 1, 2, 3
            ORDER BY 1, s.stage_name, w.project
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {
        "summary": {"refresh_id": refresh_id, "row_count": len(rows), "filters": {"stage_name": stage_name, "project": project}},
        "rows": [
            {
                "date": _json_safe(row[0]),
                "stage_name": row[1],
                "project": row[2],
                "observation_count": row[3],
                "success_count": row[4],
                "median_duration_s": _round(row[5]),
                "p95_duration_s": _round(row[6]),
                "max_duration_s": _round(row[7]),
            }
            for row in rows
        ],
    }


@app.tool()
def machine_work_failures(
    project: str | None = None,
    package: str | None = None,
    stage: str | None = None,
    limit: int = 200,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Read failed invocation, stage, and test observations with common filters."""
    from lynchpin.substrate.connection import connect, substrate_path

    if refresh_id is None:
        _ensure_work_observation_substrate_for_read(caller="machine_work_failures")

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_or_none(conn, "work_observation")
            if refresh_id is None:
                return {"summary": {"status": "missing"}, "rows": []}
        clauses = ["refresh_id = ?", "status != 'success'"]
        params: list[Any] = [refresh_id]
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        params.append(max(limit, 0))
        invocation_rows = [] if package is not None or stage is not None else conn.execute(
            f"""
            SELECT 'invocation' AS failure_kind, source_id, project, NULL AS package,
                   NULL AS stage_name, status, duration_s, command
            FROM work_observation
            WHERE {" AND ".join(clauses)}
            ORDER BY duration_s DESC NULLS LAST
            LIMIT ?
            """,
            params,
        ).fetchall()

        stage_clauses = ["s.refresh_id = ?", "NOT s.success"]
        stage_params: list[Any] = [refresh_id]
        if project is not None:
            stage_clauses.append("w.project = ?")
            stage_params.append(project)
        if stage is not None:
            stage_clauses.append("s.stage_name = ?")
            stage_params.append(stage)
        stage_params.append(max(limit, 0))
        stage_rows = [] if package is not None else conn.execute(
            f"""
            SELECT 'stage' AS failure_kind, s.source_id, w.project, NULL AS package,
                   s.stage_name, 'fail' AS status, s.duration_s, w.command
            FROM work_observation_stage s
            LEFT JOIN work_observation w
              ON w.source_id = s.invocation_source_id AND w.refresh_id = s.refresh_id
            WHERE {" AND ".join(stage_clauses)}
            ORDER BY s.duration_s DESC NULLS LAST
            LIMIT ?
            """,
            stage_params,
        ).fetchall()

        test_clauses = ["t.refresh_id = ?", "t.status NOT IN ('pass', 'success')"]
        test_params: list[Any] = [refresh_id]
        if project is not None:
            test_clauses.append("w.project = ?")
            test_params.append(project)
        if package is not None:
            test_clauses.append("t.package = ?")
            test_params.append(package)
        test_params.append(max(limit, 0))
        test_rows = [] if stage is not None else conn.execute(
            f"""
            SELECT 'test' AS failure_kind, t.source_id, w.project, t.package,
                   NULL AS stage_name, t.status, t.duration_s, w.command
            FROM work_observation_test_result t
            LEFT JOIN work_observation w
              ON w.source_id = t.invocation_source_id AND w.refresh_id = t.refresh_id
            WHERE {" AND ".join(test_clauses)}
            ORDER BY t.duration_s DESC NULLS LAST
            LIMIT ?
            """,
            test_params,
        ).fetchall()

    rows = invocation_rows + stage_rows + test_rows
    rows.sort(key=lambda row: float(row[6] or 0.0), reverse=True)
    rows = rows[:max(limit, 0)]
    return {
        "summary": {
            "refresh_id": refresh_id,
            "row_count": len(rows),
            "filters": {"project": project, "package": package, "stage": stage},
        },
        "rows": [
            {
                "failure_kind": row[0],
                "source_id": row[1],
                "project": row[2],
                "package": row[3],
                "stage_name": row[4],
                "status": row[5],
                "duration_s": _round(row[6]),
                "command": list(row[7] or ()),
            }
            for row in rows
        ],
    }


@app.tool()
def machine_feature_frames(limit: int = 100) -> dict[str, Any]:
    """Read leakage-aware machine attribution feature-frame rows."""
    payload = _analysis_artifact("machine_analysis_feature_frames.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "rows": []}
    frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
    rows = [row for row in frame.get("rows", []) if isinstance(row, dict)]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "frame_id": frame.get("frame_id"),
            "unit_type": frame.get("unit_type"),
            "row_count": frame.get("row_count", len(rows)),
            "outcome_metric": frame.get("outcome_metric"),
            "leakage_status": frame.get("leakage_status"),
            "missing_value_count": frame.get("missing_value_count"),
            "censored_count": frame.get("censored_count"),
            "caveats": frame.get("caveats", []),
        },
        "rows": rows[:max(limit, 0)],
    }


@app.tool()
def machine_feature_frame_preview(
    unit_type: str = "work_observation_stage",
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Build a live feature-frame preview for stage or invocation work units."""
    from datetime import date

    from lynchpin.analysis.machine.feature_frames import analyze_machine_feature_frames
    from lynchpin.substrate.connection import substrate_path

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    frame = analyze_machine_feature_frames(
        start=start_d,
        end=end_d,
        path=substrate_path(),
        refresh_id=refresh_id,
        unit_type=unit_type,
        limit=min(max(limit, 1), 10_000),
    )
    payload = frame.to_dict()
    payload["rows"] = payload["rows"][: min(max(limit, 0), 10_000)]
    return _json_safe(payload)


@app.tool()
def machine_mining_scans(limit: int = 100) -> dict[str, Any]:
    """Read machine mining scan registry, cohort, lagged exposure, and anomaly summaries."""
    payload = _analysis_artifact("machine_mining.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "scan": None, "cohorts": [], "lagged_exposures": [], "anomaly_clusters": []}
    cohorts = [row for row in payload.get("cohorts", []) if isinstance(row, dict)]
    lagged = [row for row in payload.get("lagged_exposures", []) if isinstance(row, dict)]
    clusters = [row for row in payload.get("anomaly_clusters", []) if isinstance(row, dict)]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "cohort_count": payload.get("cohort_count", len(cohorts)),
            "lagged_exposure_count": payload.get("lagged_exposure_count", len(lagged)),
            "anomaly_cluster_count": payload.get("anomaly_cluster_count", len(clusters)),
            "caveats": payload.get("caveats", []),
        },
        "scan": payload.get("scan"),
        "cohorts": cohorts[:max(limit, 0)],
        "lagged_exposures": lagged[:max(limit, 0)],
        "anomaly_clusters": clusters[:max(limit, 0)],
    }


@app.tool()
def machine_lagged_exposures(limit: int = 100, project: str | None = None, pressure_metric: str | None = None) -> dict[str, Any]:
    """Read exploratory lagged pressure exposure summaries from machine mining."""
    payload = _analysis_artifact("machine_mining.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "lagged_exposures": []}
    rows = [row for row in payload.get("lagged_exposures", []) if isinstance(row, dict)]
    if project:
        rows = [
            row for row in rows
            if isinstance(row.get("dimensions"), dict) and row["dimensions"].get("project") == project
        ]
    if pressure_metric:
        rows = [row for row in rows if row.get("pressure_metric") == pressure_metric]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "lagged_exposure_count": payload.get("lagged_exposure_count", len(rows)),
            "caveats": payload.get("caveats", []),
        },
        "lagged_exposures": rows[:max(limit, 0)],
    }


@app.tool()
def machine_anomaly_clusters(limit: int = 100, project: str | None = None) -> dict[str, Any]:
    """Read recurring tail/anomaly clusters from machine mining."""
    payload = _analysis_artifact("machine_mining.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "anomaly_clusters": []}
    rows = [row for row in payload.get("anomaly_clusters", []) if isinstance(row, dict)]
    if project:
        rows = [
            row for row in rows
            if isinstance(row.get("dimensions"), dict) and row["dimensions"].get("project") == project
        ]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "anomaly_cluster_count": payload.get("anomaly_cluster_count", len(rows)),
            "caveats": payload.get("caveats", []),
        },
        "anomaly_clusters": rows[:max(limit, 0)],
    }


@app.tool()
def machine_dataset_diagnostics(kind: str | None = None, severity: str | None = None) -> dict[str, Any]:
    """Read extant machine/work dataset mining diagnostics."""
    payload = _analysis_artifact("machine_dataset_diagnostics.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "diagnostics": []}
    rows = [row for row in payload.get("diagnostics", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("diagnostic_kind") == kind]
    if severity:
        rows = [row for row in rows if row.get("severity") == severity]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "diagnostic_count": payload.get("diagnostic_count", len(rows)),
            "feature_status": (payload.get("feature_audit") or {}).get("status")
            if isinstance(payload.get("feature_audit"), dict)
            else None,
            "multiplicity_status": (payload.get("mining_audit") or {}).get("multiplicity_status")
            if isinstance(payload.get("mining_audit"), dict)
            else None,
            "caveats": payload.get("caveats", []),
        },
        "feature_audit": payload.get("feature_audit"),
        "mining_audit": payload.get("mining_audit"),
        "diagnostics": rows,
    }


@app.tool()
def machine_observation_cohorts(
    limit: int = 100,
    dimension: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Read mined observational cohorts from the machine mining artifact."""
    payload = _analysis_artifact("machine_mining.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "cohorts": []}
    cohorts = [row for row in payload.get("cohorts", []) if isinstance(row, dict)]
    if dimension:
        cohorts = [row for row in cohorts if dimension in (row.get("dimensions") or {})]
    if project:
        cohorts = [
            row for row in cohorts
            if isinstance(row.get("dimensions"), dict) and row["dimensions"].get("project") == project
        ]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "cohort_count": payload.get("cohort_count", len(cohorts)),
            "scan": payload.get("scan"),
            "caveats": payload.get("caveats", []),
        },
        "cohorts": cohorts[:max(limit, 0)],
    }


@app.tool()
def machine_validation_design(limit: int = 100) -> dict[str, Any]:
    """Read discovery/validation split and boundary candidates."""
    payload = _analysis_artifact("machine_validation_design.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "split": None, "boundaries": []}
    boundaries = [row for row in payload.get("boundaries", []) if isinstance(row, dict)]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "boundary_count": payload.get("boundary_count", len(boundaries)),
            "caveats": payload.get("caveats", []),
        },
        "split": payload.get("split"),
        "boundaries": boundaries[:max(limit, 0)],
    }


@app.tool()
def machine_discovery_validation_splits(
    candidate_id: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Read discovery/validation split metadata for machine mining designs."""
    del candidate_id
    payload = _analysis_artifact("machine_validation_design.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "split": None, "boundaries": []}
    boundaries = [row for row in payload.get("boundaries", []) if isinstance(row, dict)]
    if project:
        boundaries = [
            row for row in boundaries
            if isinstance(row.get("dimensions"), dict) and row["dimensions"].get("project") == project
        ]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "boundary_count": payload.get("boundary_count", len(boundaries)),
            "caveats": payload.get("caveats", []),
        },
        "split": payload.get("split"),
        "boundaries": boundaries,
    }


@app.tool()
def machine_boundary_candidates(
    limit: int = 100,
    boundary_type: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Read candidate natural-experiment boundaries from validation design."""
    payload = _analysis_artifact("machine_validation_design.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "boundaries": []}
    boundaries = [row for row in payload.get("boundaries", []) if isinstance(row, dict)]
    if boundary_type:
        boundaries = [row for row in boundaries if row.get("boundary_type") == boundary_type]
    if project:
        boundaries = [
            row for row in boundaries
            if isinstance(row.get("dimensions"), dict) and row["dimensions"].get("project") == project
        ]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "boundary_count": payload.get("boundary_count", len(boundaries)),
            "caveats": payload.get("caveats", []),
        },
        "boundaries": boundaries[:max(limit, 0)],
    }


@app.tool()
def machine_matched_designs(limit: int = 100, status: str | None = None) -> dict[str, Any]:
    """Read matched boundary designs, placebo probes, and balance diagnostics."""
    payload = _analysis_artifact("machine_matched_designs.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "designs": []}
    designs = [row for row in payload.get("designs", []) if isinstance(row, dict)]
    if status:
        designs = [row for row in designs if row.get("identification_status") == status]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "design_count": payload.get("design_count", len(designs)),
            "supportable_design_count": payload.get("supportable_design_count"),
            "caveats": payload.get("caveats", []),
        },
        "designs": designs[:max(limit, 0)],
    }


@app.tool()
def machine_matched_comparisons(
    limit: int = 100,
    candidate_id: str | None = None,
    boundary_id: str | None = None,
) -> dict[str, Any]:
    """Read matched comparison designs, optionally restricted by candidate or boundary."""
    payload = _analysis_artifact("machine_matched_designs.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "comparisons": []}
    designs = [row for row in payload.get("designs", []) if isinstance(row, dict)]
    if candidate_id:
        designs = [row for row in designs if row.get("candidate_id") == candidate_id]
    if boundary_id:
        designs = [row for row in designs if row.get("boundary_id") == boundary_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "comparison_count": payload.get("design_count", len(designs)),
            "supportable_design_count": payload.get("supportable_design_count"),
            "caveats": payload.get("caveats", []),
        },
        "comparisons": designs[:max(limit, 0)],
    }


@app.tool()
def machine_negative_controls(limit: int = 100, status: str | None = None, boundary_id: str | None = None) -> dict[str, Any]:
    """Read negative-control and placebo checks over matched boundary designs."""
    payload = _analysis_artifact("machine_negative_controls.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "controls": []}
    rows = [row for row in payload.get("controls", []) if isinstance(row, dict)]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    if boundary_id:
        rows = [row for row in rows if row.get("boundary_id") == boundary_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "control_count": payload.get("control_count", len(rows)),
            "by_status": payload.get("by_status", {}),
            "caveats": payload.get("caveats", []),
        },
        "controls": rows[:max(limit, 0)],
    }


@app.tool()
def machine_comparisons(limit: int = 100, signal: str | None = None) -> dict[str, Any]:
    """Read observational cohort-vs-rest machine contrast estimates."""
    payload = _analysis_artifact("machine_comparisons.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "contrasts": []}
    contrasts = [row for row in payload.get("contrasts", []) if isinstance(row, dict)]
    if signal:
        contrasts = [row for row in contrasts if row.get("statistical_signal") == signal]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "contrast_count": payload.get("contrast_count", len(contrasts)),
            "multiplicity_policy": payload.get("multiplicity_policy"),
            "caveats": payload.get("caveats", []),
        },
        "contrasts": contrasts[:max(limit, 0)],
    }


@app.tool()
def machine_attribution_candidates(
    limit: int = 25,
    validation_status: str | None = None,
    mechanism_family: str | None = None,
    pareto_frontier: bool | None = None,
) -> dict[str, Any]:
    """Read non-causal machine attribution candidates from the analysis artifact."""
    payload = _analysis_artifact("machine_attribution_candidates.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "candidates": []}
    candidates = [row for row in payload.get("candidates", []) if isinstance(row, dict)]
    if validation_status:
        candidates = [row for row in candidates if row.get("validation_status") == validation_status]
    if mechanism_family:
        candidates = [row for row in candidates if row.get("mechanism_family") == mechanism_family]
    if pareto_frontier is not None:
        candidates = [row for row in candidates if bool(row.get("pareto_frontier")) is pareto_frontier]
    candidates.sort(key=lambda row: -float(row.get("priority_score") or 0.0))
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "candidate_count": payload.get("candidate_count", len(candidates)),
            "pareto_frontier_count": payload.get("pareto_frontier_count"),
            "pareto_frontier_ids": payload.get("pareto_frontier_ids", []),
            "by_validation_status": _count_by(candidates, "validation_status"),
            "by_mechanism_family": _count_by(candidates, "mechanism_family"),
            "caveats": payload.get("caveats", []),
        },
        "candidates": candidates[:max(limit, 0)],
    }


@app.tool()
def machine_benchmark_plans(
    limit: int = 25,
    status: str | None = None,
    run_group_id: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Read dry-run controlled benchmark plans generated from candidates."""
    payload = _analysis_artifact("machine_benchmark_plans.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "plans": []}
    plans = [row for row in payload.get("plans", []) if isinstance(row, dict)]
    if status:
        plans = [row for row in plans if row.get("planning_status") == status]
    if run_group_id:
        plans = [
            row for row in plans
            if isinstance(row.get("manifest_preview"), dict)
            and (row["manifest_preview"].get("controlled_benchmark") or {}).get("run_group_id") == run_group_id
        ]
    if candidate_id:
        plans = [row for row in plans if row.get("candidate_id") == candidate_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "plan_count": payload.get("plan_count", len(plans)),
            "ready_plan_count": payload.get("ready_plan_count"),
            "caveats": payload.get("caveats", []),
        },
        "plans": plans[:max(limit, 0)],
    }


@app.tool()
def machine_benchmark_plan_template(candidate_id: str) -> dict[str, Any]:
    """Return the benchmark manifest preview for a single candidate."""
    payload = _analysis_artifact("machine_benchmark_plans.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "plan": None, "manifest_preview": None}
    for row in payload.get("plans", []):
        if isinstance(row, dict) and row.get("candidate_id") == candidate_id:
            return {
                "summary": {
                    "generated_at_utc": payload.get("generated_at_utc"),
                    "generated_for": payload.get("generated_for"),
                    "candidate_id": candidate_id,
                    "planning_status": row.get("planning_status"),
                    "readiness": row.get("readiness"),
                },
                "plan": row,
                "manifest_preview": row.get("manifest_preview"),
                "run_manifest": row.get("run_manifest", []),
            }
    return {"summary": {"status": "not_found", "candidate_id": candidate_id}, "plan": None, "manifest_preview": None}


@app.tool()
def machine_benchmark_manifest_bundle(limit: int = 10) -> dict[str, Any]:
    """Read exportable benchmark manifest templates for ready plans."""
    payload = _analysis_artifact("machine_benchmark_manifest_bundle.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "groups": []}
    groups = [row for row in payload.get("groups", []) if isinstance(row, dict)]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "group_count": payload.get("group_count", len(groups)),
            "run_template_count": payload.get("run_template_count"),
            "caveats": payload.get("caveats", []),
        },
        "groups": groups[:max(limit, 0)],
    }


@app.tool()
def machine_benchmark_execution_handoff(limit: int = 10, ready_only: bool = False) -> dict[str, Any]:
    """Read ranked benchmark groups ready for future export/execution."""
    payload = _analysis_artifact("machine_benchmark_execution_handoff.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "items": []}
    rows = [row for row in payload.get("items", []) if isinstance(row, dict)]
    if ready_only:
        rows = [row for row in rows if row.get("ready_to_export") is True]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "handoff_count": payload.get("handoff_count", len(rows)),
            "ready_group_count": payload.get("ready_group_count"),
            "blocked_group_count": payload.get("blocked_group_count"),
            "run_template_count": payload.get("run_template_count"),
            "ready_run_count": payload.get("ready_run_count"),
            "caveats": payload.get("caveats", []),
        },
        "items": rows[:max(limit, 0)],
    }


@app.tool()
def machine_benchmark_selected_runbook(
    run_group_id: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Return the operational command sequence for executing one ready benchmark group."""
    payload = _analysis_artifact("machine_benchmark_execution_handoff.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "commands": []}
    rows = [row for row in payload.get("items", []) if isinstance(row, dict)]
    rows = [
        row for row in rows
        if row.get("ready_to_export") is True
        and (run_group_id is None or row.get("run_group_id") == run_group_id)
        and (candidate_id is None or row.get("candidate_id") == candidate_id)
    ]
    rows.sort(key=lambda row: (not bool(row.get("pareto_frontier")), -float(row.get("priority_score") or 0), str(row.get("run_group_id") or "")))
    if not rows:
        return {
            "summary": {
                "status": "not_found",
                "run_group_id": run_group_id,
                "candidate_id": candidate_id,
            },
            "commands": [],
        }
    row = rows[0]
    command = [
        "python",
        "-m",
        "lynchpin.analysis",
        "machine-benchmark-run-selected",
        "--run-group-id",
        str(row.get("run_group_id")),
        "--execute",
        "--materialize-after",
    ]
    return {
        "summary": {
            "status": "ready",
            "run_group_id": row.get("run_group_id"),
            "candidate_id": row.get("candidate_id"),
            "primary_metric": row.get("primary_metric"),
            "run_count": row.get("run_count"),
            "ready_run_count": row.get("ready_run_count"),
        },
        "commands": [" ".join(command)],
        "dry_run_command": " ".join(part for part in command if part not in {"--execute", "--materialize-after"}),
        "caveats": [
            "run the dry-run command first to inspect exported scripts",
            "the execute command runs generated run.sh files and then materializes coherent machine analysis",
        ],
    }


@app.tool()
def machine_below_export_handoff(limit: int = 10, kind: str | None = None) -> dict[str, Any]:
    """Read planned live-below export windows for residual pressure episodes."""
    payload = _analysis_artifact("machine_below_export_handoff.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "items": []}
    rows = [row for row in payload.get("items", []) if isinstance(row, dict)]
    failed = [row for row in payload.get("failed_captures", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("episode_kind") == kind]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "planned_window_count": payload.get("planned_window_count", len(rows)),
            "failed_capture_count": payload.get("failed_capture_count", len(failed)),
            "root": payload.get("root"),
            "live_store": payload.get("live_store"),
            "caveats": payload.get("caveats", []),
        },
        "items": rows[:max(limit, 0)],
        "failed_captures": failed[:max(limit, 0)],
    }


@app.tool()
def machine_experiment_manifest_diagnostics(
    limit: int = 100,
    kind: str | None = None,
    source_loadable: bool | None = None,
    controlled_valid: bool | None = None,
) -> dict[str, Any]:
    """Read raw experiment-manifest ingestion diagnostics."""
    payload = _analysis_artifact("machine_experiment_manifest_diagnostics.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "diagnostics": []}
    rows = [row for row in payload.get("diagnostics", []) if isinstance(row, dict)]
    if kind:
        rows = [row for row in rows if row.get("manifest_kind") == kind]
    if source_loadable is not None:
        rows = [row for row in rows if bool(row.get("source_loadable")) is source_loadable]
    if controlled_valid is not None:
        rows = [row for row in rows if bool(row.get("controlled_benchmark_valid")) is controlled_valid]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "root": payload.get("root"),
            "root_exists": payload.get("root_exists"),
            "manifest_count": payload.get("manifest_count"),
            "source_loadable_count": payload.get("source_loadable_count"),
            "controlled_benchmark_valid_count": payload.get("controlled_benchmark_valid_count"),
            "validation_issue_count": payload.get("validation_issue_count"),
            "promotion_issue_count": payload.get("promotion_issue_count"),
            "controlled_run_invalid_count": payload.get("controlled_run_invalid_count"),
            "legacy_observational_count": payload.get("legacy_observational_count"),
            "by_kind": payload.get("by_kind", {}),
            "caveats": payload.get("caveats", []),
        },
        "diagnostics": rows[:max(limit, 0)],
    }


@app.tool()
def machine_benchmark_readiness(
    payload_json: str | None = None,
    manifest_path: str | None = None,
    require_file_refs: bool = False,
) -> dict[str, Any]:
    """Validate a benchmark manifest payload or file without executing it."""
    from lynchpin.analysis.machine.controlled_benchmarks import (
        benchmark_readiness,
        validate_executed_benchmark_manifest,
    )

    path = Path(manifest_path) if manifest_path else None
    if payload_json is not None:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            return {"status": "invalid_json", "issues": [str(exc)]}
    elif path is not None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "invalid_json", "path": str(path), "issues": [str(exc)]}
    else:
        return {"status": "missing_input", "issues": ["provide payload_json or manifest_path"]}

    if not isinstance(payload, dict):
        return {"status": "invalid_payload", "issues": ["benchmark manifest payload must be an object"]}
    readiness = benchmark_readiness(payload).to_dict()
    validation = validate_executed_benchmark_manifest(
        payload,
        manifest_path=path,
        require_file_refs=require_file_refs,
    ).to_dict()
    return _json_safe({
        "status": "ok",
        "path": str(path) if path is not None else None,
        "readiness": readiness,
        "executed_manifest_validation": validation,
    })


@app.tool()
def machine_derivation_inventory(limit: int = 100, project: str | None = None) -> dict[str, Any]:
    """Read fixed Nix derivation targets available for benchmark plans."""
    payload = _analysis_artifact("machine_derivation_inventory.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "targets": []}
    targets = [row for row in payload.get("targets", []) if isinstance(row, dict)]
    if project:
        targets = [row for row in targets if row.get("project") == project]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "target_count": payload.get("target_count", len(targets)),
            "ready_target_count": payload.get("ready_target_count"),
            "caveats": payload.get("caveats", []),
        },
        "targets": targets[:max(limit, 0)],
    }


@app.tool()
def machine_support_assessments(limit: int = 25, support_level: str | None = None) -> dict[str, Any]:
    """Read support/refusal assessments for machine attribution candidates."""
    payload = _analysis_artifact("machine_support_assessment.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "assessments": []}
    rows = [row for row in payload.get("assessments", []) if isinstance(row, dict)]
    if support_level:
        rows = [row for row in rows if row.get("support_level") == support_level]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "assessment_count": payload.get("assessment_count", len(rows)),
            "refusal_count": payload.get("refusal_count"),
            "controlled_claim_count": payload.get("controlled_claim_count"),
            "natural_experiment_support_count": payload.get("natural_experiment_support_count"),
            "ready_plan_count": payload.get("ready_plan_count"),
            "run_template_count": payload.get("run_template_count"),
            "by_support_level": _count_by(rows, "support_level"),
            "caveats": payload.get("caveats", []),
        },
        "assessments": rows[:max(limit, 0)],
    }


@app.tool()
def machine_attribution_candidate_details(candidate_id: str) -> dict[str, Any]:
    """Join candidate, plan, support, mechanism, gap, and claim rows for one candidate."""
    candidates = _analysis_artifact("machine_attribution_candidates.json") or {}
    plans = _analysis_artifact("machine_benchmark_plans.json") or {}
    support = _analysis_artifact("machine_support_assessment.json") or {}
    bundle = _analysis_artifact("machine_benchmark_manifest_bundle.json") or {}
    preflight = _analysis_artifact("machine_benchmark_preflight.json") or {}
    mechanisms = _analysis_artifact("machine_mechanism_hypotheses.json") or {}
    gaps = _analysis_artifact("machine_instrumentation_gaps.json") or {}
    claims = _analysis_artifact("machine_attribution_claims.json") or {}

    candidate = next(
        (row for row in candidates.get("candidates", []) if isinstance(row, dict) and row.get("candidate_id") == candidate_id),
        None,
    )
    assessment_rows = [
        row for row in support.get("assessments", [])
        if isinstance(row, dict) and row.get("candidate_id") == candidate_id
    ]
    mechanism_ids = {
        str(row.get("mechanism", {}).get("mechanism_id"))
        for row in assessment_rows
        if isinstance(row.get("mechanism"), dict) and row.get("mechanism", {}).get("mechanism_id")
    }
    plan_rows = [
        row for row in plans.get("plans", [])
        if isinstance(row, dict) and row.get("candidate_id") == candidate_id
    ]
    run_group_ids = {
        str((row.get("manifest_preview", {}).get("controlled_benchmark") or {}).get("run_group_id"))
        for row in plan_rows
        if isinstance(row.get("manifest_preview"), dict)
        and (row.get("manifest_preview", {}).get("controlled_benchmark") or {}).get("run_group_id")
    }
    return {
        "summary": {
            "status": "found" if candidate is not None or assessment_rows else "not_found",
            "candidate_id": candidate_id,
            "run_group_ids": sorted(run_group_ids),
        },
        "candidate": candidate,
        "plans": plan_rows,
        "manifest_groups": [
            row for row in bundle.get("groups", [])
            if isinstance(row, dict) and row.get("run_group_id") in run_group_ids
        ],
        "preflight_runs": [
            row for row in preflight.get("runs", [])
            if isinstance(row, dict) and row.get("run_group_id") in run_group_ids
        ],
        "support_assessments": assessment_rows,
        "mechanisms": [
            row for row in mechanisms.get("mechanisms", [])
            if isinstance(row, dict)
            and (candidate_id in row.get("candidate_ids", []) or row.get("mechanism_id") in mechanism_ids)
        ],
        "instrumentation_gaps": [
            row for row in gaps.get("gaps", [])
            if isinstance(row, dict) and row.get("candidate_id") == candidate_id
        ],
        "attribution_claims": [
            row for row in claims.get("claims", [])
            if isinstance(row, dict) and candidate_id in row.get("source_ids", [])
        ],
    }


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if value:
            label = str(value)
            counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def _manifest_validation_status(row: dict[str, Any]) -> str | None:
    payload = row.get("manifest_validation")
    if not isinstance(payload, dict):
        return None
    if payload.get("valid") is True:
        return "valid"
    if payload.get("valid") is False:
        return "invalid"
    if "valid" in payload:
        return "unknown"
    status = payload.get("status") or payload.get("validation_status")
    return str(status) if status else None


def _count_manifest_validation_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = _manifest_validation_status(row)
        if status:
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


@app.tool()
def machine_mechanism_hypotheses(
    limit: int = 25,
    family: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Read falsifiable mechanism hypotheses grouped from support assessments."""
    payload = _analysis_artifact("machine_mechanism_hypotheses.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "mechanisms": []}
    rows = [row for row in payload.get("mechanisms", []) if isinstance(row, dict)]
    if family:
        rows = [row for row in rows if row.get("mechanism_family") == family]
    if candidate_id:
        rows = [row for row in rows if candidate_id in row.get("candidate_ids", [])]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "mechanism_count": payload.get("mechanism_count", len(rows)),
            "caveats": payload.get("caveats", []),
        },
        "mechanisms": rows[:max(limit, 0)],
    }


@app.tool()
def machine_instrumentation_gaps(
    limit: int = 50,
    project: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Read instrumentation gaps that block machine attribution support upgrades."""
    payload = _analysis_artifact("machine_instrumentation_gaps.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "gaps": []}
    rows = [row for row in payload.get("gaps", []) if isinstance(row, dict)]
    if project:
        rows = [row for row in rows if row.get("project") == project]
    if source:
        rows = [row for row in rows if row.get("missing_source") == source]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "gap_count": payload.get("gap_count", len(rows)),
            "by_missing_source": payload.get("by_missing_source", {}),
            "by_mechanism_family": payload.get("by_mechanism_family", {}),
            "caveats": payload.get("caveats", []),
        },
        "gaps": rows[:max(limit, 0)],
    }


@app.tool()
def machine_attribution_claims(
    limit: int = 25,
    support_level: str | None = None,
    project: str | None = None,
    metric: str | None = None,
) -> dict[str, Any]:
    """Read promoted machine attribution claim/refusal ledger rows."""
    payload = _analysis_artifact("machine_attribution_claims.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "claims": []}
    rows = [row for row in payload.get("claims", []) if isinstance(row, dict)]
    if support_level:
        rows = [row for row in rows if row.get("support_level") == support_level]
    if project:
        rows = [row for row in rows if row.get("project") == project]
    if metric:
        rows = [row for row in rows if (row.get("payload") or {}).get("metric") == metric]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "claim_count": payload.get("claim_count", len(rows)),
            "by_support_level": payload.get("by_support_level", {}),
            "filters": {"support_level": support_level, "project": project, "metric": metric},
            "caveats": payload.get("caveats", []),
        },
        "claims": rows[:max(limit, 0)],
    }


@app.tool()
def machine_claim_evidence(claim_id: str) -> dict[str, Any]:
    """Join one machine attribution claim to assumptions and upstream evidence ids."""
    claims = _analysis_artifact("machine_attribution_claims.json") or {}
    assumptions = _analysis_artifact("machine_assumption_checks.json") or {}
    gaps = _analysis_artifact("machine_instrumentation_gaps.json") or {}
    support = _analysis_artifact("machine_support_assessment.json") or {}
    matched = _analysis_artifact("machine_matched_designs.json") or {}
    negative = _analysis_artifact("machine_negative_controls.json") or {}
    claim = next(
        (row for row in claims.get("claims", []) if isinstance(row, dict) and row.get("claim_id") == claim_id),
        None,
    )
    source_ids = set(claim.get("source_ids", [])) if isinstance(claim, dict) else set()
    matched_designs = [
        row for row in matched.get("designs", [])
        if isinstance(row, dict) and row.get("design_id") in source_ids
    ]
    matched_design_ids = {
        str(row.get("design_id"))
        for row in matched_designs
        if row.get("design_id")
    }
    return {
        "summary": {"status": "found" if claim is not None else "not_found", "claim_id": claim_id},
        "claim": claim,
        "assumption_checks": [
            row for row in assumptions.get("checks", [])
            if isinstance(row, dict) and row.get("claim_id") == claim_id
        ],
        "instrumentation_gaps": [
            row for row in gaps.get("gaps", [])
            if isinstance(row, dict) and row.get("candidate_id") in source_ids
        ],
        "support_assessments": [
            row for row in support.get("assessments", [])
            if isinstance(row, dict)
            and (row.get("assessment_id") in source_ids or row.get("candidate_id") in source_ids)
        ],
        "matched_designs": matched_designs,
        "negative_controls": [
            row for row in negative.get("controls", [])
            if isinstance(row, dict)
            and (
                row.get("control_id") in source_ids
                or row.get("design_id") in source_ids
                or row.get("design_id") in matched_design_ids
            )
        ],
        "source_ids": sorted(str(item) for item in source_ids),
    }


@app.tool()
def machine_assumption_checks(limit: int = 50, status: str | None = None, claim_id: str | None = None) -> dict[str, Any]:
    """Read assumption checks limiting or supporting machine attribution claims."""
    payload = _analysis_artifact("machine_assumption_checks.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "checks": []}
    rows = [row for row in payload.get("checks", []) if isinstance(row, dict)]
    if status:
        rows = [row for row in rows if row.get("check_status") == status]
    if claim_id:
        rows = [row for row in rows if row.get("claim_id") == claim_id]
    return {
        "summary": {
            "generated_at_utc": payload.get("generated_at_utc"),
            "generated_for": payload.get("generated_for"),
            "check_count": payload.get("check_count", len(rows)),
            "by_status": payload.get("by_status", {}),
            "caveats": payload.get("caveats", []),
        },
        "checks": rows[:max(limit, 0)],
    }


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
    from lynchpin.substrate.machine import load_machine_service_state_summary

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        _ensure_machine_materialized_for_read(start=start_d, end=materialization_end)
        ensure_substrate_materialized_for_read(
            caller="machine_service_state_summary",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(
                conn,
                "machine_service_state",
                caller="machine_service_state_summary",
            )
            if refresh_id is None:
                return []

        rows = load_machine_service_state_summary(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            host=host,
            unit=unit,
        )

    return [
        {
            "host": row[0],
            "unit": row[1],
            "scope": row[2],
            "samples": row[3],
            "active_samples": row[4],
            "max_memory_current_bytes": row[5],
            "cpu_usage_delta_nsec": row[6],
            "io_read_delta_bytes": row[7],
            "io_write_delta_bytes": row[8],
            "first_observed_at": _json_safe(row[9]),
            "last_observed_at": _json_safe(row[10]),
            "last_cpu_usage_nsec": row[11],
            "last_io_read_bytes": row[12],
            "last_io_write_bytes": row[13],
        }
        for row in rows
    ]


@app.tool()
def borg_drill_history(
    limit: int = 50,
    status: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return random-archive deep-verify drill history written by
    sinnix-borg-drill (weekly oneshot, one row per repo per invocation).

    The drill complements borg's repository-only check by sampling
    chunk-content integrity via `borg check --verify-data`. Use this
    tool to confirm the integrity story for each repo: every row is
    one archive whose chunks were re-read and verified end-to-end.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_borg_drill_runs, load_borg_drill_summary

    with connect(substrate_path(), read_only=True) as conn:
        rows = load_borg_drill_runs(conn, limit=limit, status=status, repo=repo)
        summary_row = load_borg_drill_summary(conn)

    return {
        "summary": {
            "total": int(summary_row[0]) if summary_row else 0,
            "ok": int(summary_row[1]) if summary_row else 0,
            "failed": int(summary_row[2]) if summary_row else 0,
            "last_started_at": _json_safe(summary_row[3]) if summary_row else None,
            "filters": {"status": status, "repo": repo},
        },
        "rows": [
            {
                "repo": row[0],
                "archive": row[1],
                "started_at": _json_safe(row[2]),
                "ended_at": _json_safe(row[3]),
                "duration_s": int(row[4]),
                "exit_code": int(row[5]),
                "status": row[6],
                "within_days": int(row[7]),
            }
            for row in rows
        ],
    }


@app.tool()
def sinnix_generation_history(
    limit: int = 50,
    host: str | None = None,
) -> list[dict[str, Any]]:
    """Return the NixOS-generation activation history captured by the
    sinnix `lynchpinGenerationLog` activation script.

    Each row corresponds to one `nixos-rebuild switch` activation and
    carries {host, generation, activated_at, store_path, sinnix_revision,
    nixos_label}. Use the activated_at column to join against
    machine_metric_sample.observed_at — the latest activated_at <= a
    sample's observed_at is the generation that produced the sample.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_sinnix_generation_rows

    ensure_substrate_materialized_for_read(caller="sinnix_generation_history")

    with connect(substrate_path(), read_only=True) as conn:
        rows = load_sinnix_generation_rows(conn, limit=limit, host=host)
    return [
        {
            "host": row[0],
            "generation": row[1],
            "activated_at": _json_safe(row[2]),
            "store_path": row[3],
            "sinnix_revision": row[4],
            "nixos_label": row[5],
        }
        for row in rows
    ]


@app.tool()
def machine_bufferbloat_summary(
    start: str | None = None,
    end: str | None = None,
    interface: str | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Return per-day bufferbloat measurements from `machine_network_sample`.

    The bufferbloat probe (`bufferbloatIntervalSec=1800` on
    machine-telemetry) samples the ICMP RTT distribution to 8.8.8.8 by
    default; the result lands in the `bloat` JSON column. Most rows
    carry no `bloat` (the cadence is much slower than the per-sample
    network probe); rows that do carry it expose `avg_ms`, `min_ms`,
    `max_ms`, `loss`, `status`, `ip`.

    This tool aggregates those rows by day per interface and returns
    {sample_count, avg_ms_{p50,p95,max}, loss_{p50,p95,max}} so callers
    can spot regression windows without re-implementing the JSON
    extraction. With limited data the percentile estimates are weak —
    use `sample_count` to weight conclusions.
    """
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_bufferbloat_daily

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        _ensure_machine_materialized_for_read(start=start_d, end=materialization_end)
        ensure_substrate_materialized_for_read(
            caller="machine_bufferbloat_summary",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(
                conn,
                "machine_network_sample",
                caller="machine_bufferbloat_summary",
            )
            if refresh_id is None:
                rows = []
            else:
                rows = load_bufferbloat_daily(
                    conn,
                    refresh_id=refresh_id,
                    start=start_d,
                    end=end_d,
                    interface=interface,
                )
        else:
            rows = load_bufferbloat_daily(
                conn,
                refresh_id=refresh_id,
                start=start_d,
                end=end_d,
                interface=interface,
            )

    return {
        "summary": {
            "row_count": len(rows),
            "refresh_id": refresh_id,
            "filters": {"start": start, "end": end, "interface": interface},
        },
        "rows": [
            {
                "day": _json_safe(row[0]),
                "interface": row[1],
                "sample_count": int(row[2]),
                "avg_ms_p50": _round(row[3]),
                "avg_ms_p95": _round(row[4]),
                "avg_ms_max": _round(row[5]),
                "loss_p50": _round(row[6]),
                "loss_p95": _round(row[7]),
                "loss_max": _round(row[8]),
            }
            for row in rows
        ],
    }


def _round(value: Any, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _float_or_zero(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return round(values[midpoint], 3)
    return round((values[midpoint - 1] + values[midpoint]) / 2, 3)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    idx = min(len(values) - 1, int(len(values) * 0.95))
    return round(values[idx], 3)


def _best_refresh_or_none(conn: Any, table: str) -> str | None:
    try:
        return best_materialized_refresh_id(conn, table, caller=f"machine_gap_summary.{table}")
    except Exception:
        return None


@app.tool()
def machine_gap_summary(
    threshold_pct: float | None = None,
) -> dict[str, Any]:
    """Return per-(table, gap_code) share of recent telemetry rows that
    recorded each code, plus any code exceeding the regression threshold.

    Reads the materialized ``machine_gap_summary.json`` artifact produced
    by the daily materialization DAG; use ``threshold_pct`` to re-filter the
    ``regressions`` list to a stricter share than what the artifact was
    computed at.
    """
    payload = _analysis_artifact("machine_gap_summary.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "counts": [], "regressions": []}

    counts = [row for row in payload.get("counts", []) if isinstance(row, dict)]
    regressions = [row for row in payload.get("regressions", []) if isinstance(row, dict)]
    if threshold_pct is not None:
        regressions = [r for r in regressions if float(r.get("share_pct") or 0) >= threshold_pct]

    summary = {
        "generated_for": payload.get("generated_for", {}),
        "generated_at_utc": payload.get("generated_at_utc"),
        "count_total": len(counts),
        "regression_count": len(regressions),
        "effective_threshold_pct": threshold_pct
        if threshold_pct is not None
        else payload.get("generated_for", {}).get("regression_pct"),
    }
    return {"summary": summary, "counts": counts, "regressions": regressions}
