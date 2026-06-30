"""Machine service-state, metrics, health, telemetry, and window tools."""
from typing import Any

from lynchpin.analysis.machine.status import machine_status_payload
from lynchpin.core.errors import SchemaVersionError
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._machine_helpers import (
    _analysis_artifact,
    _artifact_rows,
    _exclusive_end,
    _required_analysis_artifact,
    _round,
    _timestamp_filter,
)
from lynchpin.mcp.tools._utils import json_safe as _json_safe


@app.tool()
def machine_status() -> dict[str, Any]:
    """Summarize current machine-analysis readiness, support, claims, and blockers."""
    return _json_safe(machine_status_payload())


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
    from datetime import datetime, timedelta, timezone

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

    # Import here to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        machine_module.ensure_substrate_materialized_for_read(
            caller="machine_metrics_daily",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = machine_module.best_materialized_refresh_id(conn, "machine_metric_sample", caller="machine_metrics_daily")
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

    # Import here to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        machine_module.ensure_substrate_materialized_for_read(
            caller="machine_metrics_by_context",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = machine_module.best_materialized_refresh_id(
                conn,
                "machine_metric_sample",
                caller="machine_metrics_by_context",
            )
            if refresh_id is None:
                return []
        generations_refresh_id = machine_module.best_materialized_refresh_id(
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
        raise SchemaVersionError(
            source="machine_episode_analysis",
            expected=str(EPISODE_DETECTOR_VERSION),
            found=str(payload.get("detector_version")),
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
        _proj_raw = row.get("projects")
        projects: list[Any] = _proj_raw if isinstance(_proj_raw, list) else []
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
def machine_telemetry_analysis(section: str = "daily", limit: int = 100) -> dict[str, Any]:
    """Read the materialized machine telemetry analysis artifact."""
    payload = _analysis_artifact("machine_telemetry_analysis.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "rows": []}
    valid_sections = {
        "daily",
        "memory_breakdown",
        "signals",
        "hardware_regimes",
        "correlations",
    }
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


def machine_memory_breakdown(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return recent decomposed memory samples from machine_metric_sample."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_machine_memory_breakdown

    # Import here to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        machine_module.ensure_substrate_materialized_for_read(
            caller="machine_memory_breakdown",
            window=(start_d, materialization_end)
            if start_d is not None and materialization_end is not None
            else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = machine_module.best_materialized_refresh_id(
                conn,
                "machine_metric_sample",
                caller="machine_memory_breakdown",
            )
            if refresh_id is None:
                return {"summary": {"row_count": 0}, "rows": []}
        rows = load_machine_memory_breakdown(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            host=host,
            limit=limit,
        )

    for row in rows:
        row["observed_at"] = _json_safe(row.get("observed_at"))
    return {
        "summary": {
            "refresh_id": refresh_id,
            "row_count": len(rows),
            "schema": "schema-v4 memory split when source_schema_version >= 4",
        },
        "rows": rows,
    }


@app.tool()
def machine_pressure_explain(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
    focus: str = "io",
    limit: int = 5,
    window_minutes: int = 5,
    top_n: int = 8,
) -> dict[str, Any]:
    """Explain pressure windows by joining memory split, service RSS, process I/O, and process PSS."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import load_machine_pressure_explainer

    # Import here to allow test patching in the machine module.
    from lynchpin.mcp.tools import machine as machine_module

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        machine_module.ensure_substrate_materialized_for_read(
            caller="machine_pressure_explain",
            window=(start_d, materialization_end)
            if start_d is not None and materialization_end is not None
            else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = machine_module.best_materialized_refresh_id(
                conn,
                "machine_metric_sample",
                caller="machine_pressure_explain",
            )
            if refresh_id is None:
                return {"summary": {"row_count": 0}, "windows": []}
        windows = load_machine_pressure_explainer(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            host=host,
            focus=focus,
            limit=limit,
            window_minutes=window_minutes,
            top_n=top_n,
        )

    return _json_safe(
        {
            "summary": {
                "refresh_id": refresh_id,
                "row_count": len(windows),
                "focus": focus,
                "window_minutes": window_minutes,
                "top_n": top_n,
                "joins": [
                    "machine_metric_sample",
                    "machine_service_state",
                    "machine_process_io_delta_sample",
                    "machine_process_memory_sample",
                ],
            },
            "windows": windows,
        }
    )


def _classify_pressure(latest: dict[str, Any], process_rows: list[dict[str, Any]]) -> str:
    if not latest and not process_rows:
        return "instrumentation-gap"
    mem_avail = latest.get("mem_avail_mb") or 0
    mem_total = latest.get("mem_total_mb") or 0
    swap_used = latest.get("swap_used_mb") or 0
    memory_full = latest.get("memory_psi_full_avg60") or 0
    anon = latest.get("mem_anon_mb") or 0
    file_cache = latest.get("mem_file_cache_mb") or 0
    if swap_used > 0 and (memory_full > 1 or (mem_total and mem_avail / mem_total < 0.2)):
        return "swap-pressure"
    if memory_full > 1:
        return "process-heavy" if anon >= file_cache else "io-thrash"
    if file_cache > anon and mem_avail > 8192:
        return "cache-heavy"
    return "ok"


def _process_workload_class(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("comm", "unit", "command_line")
    )
    if any(token in text for token in ("chrome", "chromium", "firefox")):
        return "browser"
    if any(token in text for token in ("hyprland", "kitty", "noctalia", "quickshell")):
        return "desktop-terminal"
    if any(token in text for token in ("codex", "claude", "gemini", "mcp")):
        return "agent"
    if any(token in text for token in ("nix", "cargo", "rustc", "cc1", "ld")):
        return "build"
    if any(token in text for token in ("serena", "codebase-memory")):
        return "semantic-tools"
    if any(token in text for token in ("lynchpin", "polylogue")):
        return "evidence-tools"
    return "other"


def _group_process_memory_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        workload_class = _process_workload_class(row)
        group = grouped.setdefault(
            workload_class,
            {
                "workload_class": workload_class,
                "process_count": 0,
                "pss_mib": 0.0,
                "private_mib": 0.0,
                "top_process": None,
            },
        )
        group["process_count"] += 1
        group["pss_mib"] += float(row.get("pss_mib") or 0.0)
        group["private_mib"] += float(row.get("private_mib") or 0.0)
        if group["top_process"] is None or float(row.get("pss_mib") or 0.0) > float(group["top_process"].get("pss_mib") or 0.0):
            group["top_process"] = {
                "comm": row.get("comm"),
                "unit": row.get("unit"),
                "pss_mib": row.get("pss_mib"),
            }
    return [
        {
            **group,
            "pss_mib": _round(group["pss_mib"], 1),
            "private_mib": _round(group["private_mib"], 1),
        }
        for group in sorted(grouped.values(), key=lambda item: item["pss_mib"], reverse=True)
    ]


def _memory_accounting(latest: dict[str, Any], process_rows: list[dict[str, Any]]) -> dict[str, Any]:
    mem_total = float(latest.get("mem_total_mb") or 0.0)
    mem_avail = float(latest.get("mem_avail_mb") or 0.0)
    mem_used = float(latest.get("mem_used_mb") or 0.0)
    mem_anon = float(latest.get("mem_anon_mb") or 0.0)
    file_cache = float(latest.get("mem_file_cache_mb") or 0.0)
    slab_reclaimable = float(latest.get("mem_slab_reclaimable_mb") or 0.0)
    slab_unreclaimable = float(latest.get("mem_slab_unreclaimable_mb") or 0.0)
    swap_used = float(latest.get("swap_used_mb") or 0.0)
    top_pss = sum(float(row.get("pss_mib") or 0.0) for row in process_rows)
    top_private = sum(float(row.get("private_mib") or 0.0) for row in process_rows)
    reclaimable = file_cache + slab_reclaimable
    finite_pressure_mb = mem_total - mem_avail if mem_total else mem_used
    return {
        "mem_total_mb": _round(mem_total, 1) if mem_total else None,
        "mem_used_mb": _round(mem_used, 1) if latest else None,
        "mem_avail_mb": _round(mem_avail, 1) if latest else None,
        "mem_avail_percent": _round((mem_avail / mem_total) * 100, 1)
        if mem_total
        else None,
        "finite_pressure_mb": _round(finite_pressure_mb, 1) if latest else None,
        "anon_mb": _round(mem_anon, 1) if latest else None,
        "reclaimable_cache_mb": _round(reclaimable, 1) if latest else None,
        "file_cache_mb": _round(file_cache, 1) if latest else None,
        "slab_reclaimable_mb": _round(slab_reclaimable, 1) if latest else None,
        "slab_unreclaimable_mb": _round(slab_unreclaimable, 1) if latest else None,
        "swap_used_mb": _round(swap_used, 1) if latest else None,
        "top_process_pss_mb": _round(top_pss, 1),
        "top_process_private_mb": _round(top_private, 1),
        "top_process_pss_vs_anon_percent": _round((top_pss / mem_anon) * 100, 1)
        if mem_anon
        else None,
        "top_process_sample_count": len(process_rows),
    }


@app.tool()
def machine_pressure_report(
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
    window_minutes: int = 5,
    window_limit: int = 3,
    top_n: int = 8,
) -> dict[str, Any]:
    """Compact machine pressure report with memory decomposition and PSS attribution."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.machine import (
        load_machine_memory_breakdown,
        load_machine_pressure_explainer,
        load_machine_process_memory_samples,
    )

    from lynchpin.mcp.tools import machine as machine_module

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        machine_module.ensure_substrate_materialized_for_read(
            caller="machine_pressure_report",
            window=(start_d, materialization_end)
            if start_d is not None and materialization_end is not None
            else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = machine_module.best_materialized_refresh_id(
                conn,
                "machine_metric_sample",
                caller="machine_pressure_report",
            )
            if refresh_id is None:
                return {
                    "summary": {"row_count": 0, "status": "empty"},
                    "memory": [],
                    "worst_windows": {},
                    "top_processes_by_pss": [],
                }
        memory = load_machine_memory_breakdown(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            host=host,
            limit=1,
        )
        worst_windows = {
            focus: load_machine_pressure_explainer(
                conn,
                refresh_id=refresh_id,
                start=start_d,
                end=end_d,
                host=host,
                focus=focus,
                limit=window_limit,
                window_minutes=window_minutes,
                top_n=top_n,
            )
            for focus in ("io", "memory", "cache", "swap")
        }
        processes = load_machine_process_memory_samples(
            conn,
            refresh_id=refresh_id,
            start=start_d,
            end=end_d,
            hosts=(host,) if host else None,
            limit=max(int(top_n), 0),
        )

    process_rows = [
        {
            "observed_at": sample.observed_at,
            "pid": sample.pid,
            "comm": sample.comm,
            "unit": sample.unit,
            "scope": sample.scope,
            "rss_mib": _round(sample.rss_kb / 1024, 1),
            "pss_mib": _round(sample.pss_kb / 1024, 1),
            "pss_anon_mib": _round(sample.pss_anon_kb / 1024, 1)
            if sample.pss_anon_kb is not None
            else None,
            "pss_file_mib": _round(sample.pss_file_kb / 1024, 1)
            if sample.pss_file_kb is not None
            else None,
            "pss_shmem_mib": _round(sample.pss_shmem_kb / 1024, 1)
            if sample.pss_shmem_kb is not None
            else None,
            "private_mib": _round(
                (sample.private_clean_kb + sample.private_dirty_kb) / 1024,
                1,
            ),
            "shared_mib": _round(
                (sample.shared_clean_kb + sample.shared_dirty_kb) / 1024,
                1,
            ),
            "swap_mib": _round(sample.swap_kb / 1024, 1),
            "command_line": sample.command_line,
        }
        for sample in processes
    ]
    latest = memory[0] if memory else {}
    missing_window_pss = any(
        not window.get("top_processes_by_pss")
        for windows in worst_windows.values()
        for window in windows
    )
    caveats = [
        "PSS/private attribution comes from promoted process smaps_rollup samples; "
        "kernel page cache remains host-level and is not fully assignable to a process.",
        "High mem_used can be mostly reclaimable file/slab cache; use mem_avail and PSI for pressure.",
    ]
    if missing_window_pss:
        caveats.append(
            "Some pressure windows have no process-PSS rows; they likely predate continuous process-memory capture or fall outside retained top-N samples."
        )
    return _json_safe(
        {
            "summary": {
                "refresh_id": refresh_id,
                "status": "ok" if latest or process_rows else "empty",
                "classification": _classify_pressure(latest, process_rows),
                "memory_rows": len(memory),
                "process_rows": len(process_rows),
                "window_limit": window_limit,
                "window_minutes": window_minutes,
                "top_n": top_n,
                "joins": [
                    "machine_metric_sample",
                    "machine_service_state",
                    "machine_process_io_delta_sample",
                    "machine_process_memory_sample",
                ],
            },
            "memory": latest,
            "worst_windows": worst_windows,
            "top_processes_by_pss": process_rows,
            "process_memory_groups": _group_process_memory_rows(process_rows),
            "memory_accounting": _memory_accounting(latest, process_rows),
            "interpretation": {
                "mem_used_mb": latest.get("mem_used_mb"),
                "mem_avail_mb": latest.get("mem_avail_mb"),
                "mem_anon_mb": latest.get("mem_anon_mb"),
                "mem_file_cache_mb": latest.get("mem_file_cache_mb"),
                "swap_used_mb": latest.get("swap_used_mb"),
                "memory_psi_full_avg60": latest.get("memory_psi_full_avg60"),
            },
            "caveats": caveats,
        }
    )


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
            if project in (_p if isinstance(_p := row.get("projects"), list) else [])
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

    # Import here to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        machine_module.ensure_substrate_materialized_for_read(
            caller="machine_service_state_summary",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = machine_module.best_materialized_refresh_id(
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
            "max_memory_anon_bytes": row[6],
            "max_memory_file_bytes": row[7],
            "max_memory_kernel_bytes": row[8],
            "cpu_usage_delta_nsec": row[9],
            "io_read_delta_bytes": row[10],
            "io_write_delta_bytes": row[11],
            "first_observed_at": _json_safe(row[12]),
            "last_observed_at": _json_safe(row[13]),
            "last_cpu_usage_nsec": row[14],
            "last_io_read_bytes": row[15],
            "last_io_write_bytes": row[16],
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

    # Import here to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

    machine_module.ensure_substrate_materialized_for_read(caller="sinnix_generation_history")

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

    # Import here to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

    start_d = _date.fromisoformat(start) if start else None
    end_d = _date.fromisoformat(end) if end else None
    materialization_end = _exclusive_end(end_d)
    if refresh_id is None:
        machine_module.ensure_substrate_materialized_for_read(
            caller="machine_bufferbloat_summary",
            window=(start_d, materialization_end) if start_d is not None and materialization_end is not None else None,
        )

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = machine_module.best_materialized_refresh_id(
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


@app.tool()
def machine_metrics(
    by: str = "daily",
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    refresh_id: str | None = None,
) -> Any:
    """Machine telemetry metrics. by: daily, context, memory, pressure, pressure_report."""
    if by == "daily":
        return machine_metrics_daily(start=start, end=end, host=host, refresh_id=refresh_id)
    if by == "context":
        return machine_metrics_by_context(start=start, end=end, host=host, refresh_id=refresh_id)
    if by == "memory":
        return machine_memory_breakdown(
            start=start,
            end=end,
            host=host,
            refresh_id=refresh_id,
        )
    if by == "pressure":
        return machine_pressure_explain(
            start=start,
            end=end,
            host=host,
            refresh_id=refresh_id,
        )
    if by == "pressure_report":
        return machine_pressure_report(
            start=start,
            end=end,
            host=host,
            refresh_id=refresh_id,
        )
    return {"error": f"unknown by {by!r}. choices: daily, context, memory, pressure, pressure_report"}


@app.tool()
def machine_below(
    view: str = "analysis",
    section: str = "system",
    capture_id: str | None = None,
    episode_kind: str | None = None,
    attribution_source: str = "below",
    start: str | None = None,
    end: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> Any:
    """Below-bound machine analysis. view: analysis, attributions, export_handoff."""
    if view == "analysis":
        return machine_below_analysis(section=section, capture_id=capture_id, limit=limit)
    if view == "attributions":
        return machine_below_attributions(start=start, end=end, episode_kind=episode_kind, capture_id=capture_id, attribution_source=attribution_source, limit=limit)
    if view == "export_handoff":
        from lynchpin.mcp.tools.machine_benchmarks import machine_below_export_handoff
        return machine_below_export_handoff(limit=limit, kind=kind)
    return {"error": f"unknown view {view!r}. choices: analysis, attributions, export_handoff"}


@app.tool()
def machine_service(
    view: str = "state_summary",
    invocation_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    host: str | None = None,
    unit: str | None = None,
    refresh_id: str | None = None,
    limit: int = 20,
    min_total_mib: float = 0.0,
    include_below_processes: bool = False,
    below_top_per_sample: int = 20,
) -> Any:
    """Machine service data. view: state_summary (current service state summary), io_for_xtask (service I/O for a specific xtask invocation; use invocation_id to specify)."""
    if view == "state_summary":
        return machine_service_state_summary(start=start, end=end, host=host, unit=unit, refresh_id=refresh_id)
    if view == "io_for_xtask":
        if invocation_id is None:
            return {"error": "invocation_id is required for view=io_for_xtask"}
        return machine_service_io_for_xtask_invocation(
            invocation_id=invocation_id,
            limit=limit,
            min_total_mib=min_total_mib,
            include_below_processes=include_below_processes,
            below_top_per_sample=below_top_per_sample,
        )
    return {"error": f"unknown view {view!r}. choices: state_summary, io_for_xtask"}


@app.tool()
def machine_windows(
    view: str = "context",
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    source: str | None = None,
    has_episodes: bool | None = None,
    pressure_state: str | None = None,
    work_state: str | None = None,
    limit: int = 100,
) -> Any:
    """Machine time-window data. view: context (context windows), work_state (work state windows)."""
    if view == "context":
        return machine_context_windows(
            start=start,
            end=end,
            project=project,
            source=source,
            has_episodes=has_episodes,
            limit=limit,
        )
    if view == "work_state":
        return machine_work_state_windows(
            pressure_state=pressure_state,
            work_state=work_state,
            project=project,
            limit=limit,
        )
    return {"error": f"unknown view {view!r}. choices: context, work_state"}
