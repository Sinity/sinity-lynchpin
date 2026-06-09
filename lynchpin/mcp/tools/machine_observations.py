"""Machine work observations, command performance, feature frames, and mining tools."""
from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._machine_helpers import (
    _analysis_artifact,
    _artifact_rows,
    _best_refresh_or_none,
    _ensure_work_observation_substrate_for_read,
    _float_or_zero,
    _json_safe,
    _median,
    _p95,
    _round,
    _workflow_mechanics_artifact_payload,
)
from lynchpin.mcp.tools._utils import json_safe as _json_safe


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
            (conn.execute(
                "SELECT COUNT(*) FROM work_observation_stage WHERE refresh_id = ?",
                [refresh_id],
            ).fetchone() or (0,))[0]
            or 0
        )
        test_count = int(
            (conn.execute(
                "SELECT COUNT(*) FROM work_observation_test_result WHERE refresh_id = ?",
                [refresh_id],
            ).fetchone() or (0,))[0]
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
        "caveats": sorted(set([  # type: ignore[arg-type]
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

    # Import here to allow test patching in the machine module
    from lynchpin.mcp.tools import machine as machine_module

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
            refresh_id = machine_module._best_refresh_or_none(conn, "work_observation")
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


def _machine_feature_frames_list(limit: int = 100) -> dict[str, Any]:
    """Read leakage-aware machine attribution feature-frame rows."""
    payload = _analysis_artifact("machine_analysis_feature_frames.json")
    if payload is None:
        return {"summary": {"status": "missing"}, "rows": []}
    _frame_raw = payload.get("frame")
    frame: dict[str, Any] = _frame_raw if isinstance(_frame_raw, dict) else {}
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
def machine_work_observations(
    view: str = "daily",
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    command_contains: str | None = None,
    stage_name: str | None = None,
    package: str | None = None,
    stage: str | None = None,
    refresh_id: str | None = None,
    retry_gap_min: int = 20,
    limit: int = 500,
) -> Any:
    """Work observation data. view: daily, mechanics, stage_summary, test_summary, artifact, slow_tests, stage_daily, failures."""
    if view == "daily":
        return machine_work_observation_daily(start=start, end=end, project=project, command_contains=command_contains, refresh_id=refresh_id, limit=limit)
    if view == "mechanics":
        return machine_workflow_mechanics(start=start, end=end, project=project, refresh_id=refresh_id, retry_gap_min=retry_gap_min, limit=limit)
    if view == "stage_summary":
        return machine_work_stage_summary(start=start, end=end, stage_name=stage_name, refresh_id=refresh_id, limit=limit)
    if view == "test_summary":
        return machine_work_test_summary(package=package, refresh_id=refresh_id, limit=limit)
    if view == "artifact":
        return machine_work_observation_artifact()
    if view == "slow_tests":
        return machine_work_slow_tests(package=package, project=project, limit=limit, refresh_id=refresh_id)
    if view == "stage_daily":
        return machine_work_stage_daily(stage_name=stage_name, project=project, limit=limit, refresh_id=refresh_id)
    if view == "failures":
        return machine_work_failures(project=project, package=package, stage=stage, limit=limit, refresh_id=refresh_id)
    return {"error": f"unknown view {view!r}. choices: daily, mechanics, stage_summary, test_summary, artifact, slow_tests, stage_daily, failures"}


@app.tool()
def machine_mining(
    view: str = "scans",
    limit: int = 100,
    project: str | None = None,
    pressure_metric: str | None = None,
    dimension: str | None = None,
) -> Any:
    """Machine mining results. view: scans, exposures, clusters, cohorts."""
    if view == "scans":
        return machine_mining_scans(limit=limit)
    if view == "exposures":
        return machine_lagged_exposures(limit=limit, project=project, pressure_metric=pressure_metric)
    if view == "clusters":
        return machine_anomaly_clusters(limit=limit, project=project)
    if view == "cohorts":
        return machine_observation_cohorts(limit=limit, dimension=dimension, project=project)
    return {"error": f"unknown view {view!r}. choices: scans, exposures, clusters, cohorts"}


@app.tool()
def machine_observational(
    view: str = "deltas",
    tool: str | None = None,
    work_state: str | None = None,
    pressure_state: str | None = None,
    dimension: str | None = None,
    key: str | None = None,
    limit: int = 100,
) -> Any:
    """Observational machine baselines and deltas. view: deltas, baselines."""
    if view == "deltas":
        return machine_observational_deltas(tool=tool, work_state=work_state, pressure_state=pressure_state, limit=limit)
    if view == "baselines":
        return machine_observational_baselines(dimension=dimension, key=key, limit=limit)
    return {"error": f"unknown view {view!r}. choices: deltas, baselines"}


@app.tool()
def machine_feature_frames(
    view: str = "frames",
    limit: int = 100,
    unit_type: str = "work_observation_stage",
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
) -> Any:
    """Machine feature frames. view: frames (list feature frames), preview (feature frame preview detail for a specific unit_type)."""
    if view == "frames":
        return _machine_feature_frames_list(limit=limit)
    if view == "preview":
        return machine_feature_frame_preview(
            unit_type=unit_type,
            start=start,
            end=end,
            refresh_id=refresh_id,
            limit=limit,
        )
    return {"error": f"unknown view {view!r}. choices: frames, preview"}
