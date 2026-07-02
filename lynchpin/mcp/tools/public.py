"""Collapsed public MCP surface for Lynchpin.

NOTE: do NOT add ``from __future__ import annotations`` here. FastMCP
introspects annotations at decoration time.
"""

import re
import inspect
from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Any

from lynchpin.mcp.registry import (
    PUBLIC_TOOL_NAMES,
    PUBLIC_TOOLS,
    public_action_spec,
    public_action_names,
    public_tool_catalog,
)
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import json_safe

_CURRENT_ROUTE: ContextVar[tuple[str, str] | None] = ContextVar("lynchpin_mcp_current_route", default=None)


def _ok(data: Any, **meta: Any) -> dict[str, Any]:
    return {"ok": True, "data": json_safe(data), "meta": json_safe(meta)}


def _error(code: str, message: str, *, choices: list[str] | tuple[str, ...] = (), hint: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "message": message,
        "choices": list(choices),
        "hint": hint,
    }


def _invalid_action(tool_name: str, action: str) -> dict[str, Any]:
    return _error(
        "invalid_action",
        f"unknown action {action!r} for {tool_name}",
        choices=public_action_names(tool_name),
        hint="Call lynchpin_catalog() for action metadata.",
    )


def _action_meta(tool_name: str, action: str, *, route: str | None = None, **meta: Any) -> dict[str, Any]:
    spec = public_action_spec(tool_name, action)
    payload: dict[str, Any] = {
        "tool": tool_name,
        "action": action,
        "effect_mode": spec.effect_mode if spec else None,
    }
    if route:
        payload["route"] = route
    payload.update(meta)
    return payload


def _mark_route(tool_name: str, action: str) -> dict[str, Any] | None:
    invalid = _require_action(tool_name, action)
    if invalid is not None:
        return invalid
    _CURRENT_ROUTE.set((tool_name, action))
    return None


def _current_meta(*, route: str | None = None, **meta: Any) -> dict[str, Any]:
    current = _CURRENT_ROUTE.get()
    if current is None:
        return {"route": route, **meta} if route else dict(meta)
    tool_name, action = current
    return _action_meta(tool_name, action, route=route, **meta)


def _require_action(tool_name: str, action: str) -> dict[str, Any] | None:
    if public_action_spec(tool_name, action) is None:
        return _invalid_action(tool_name, action)
    return None


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _date_window(start: str | None, end: str | None) -> tuple[date, date] | None:
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if start_d is None or end_d is None:
        return None
    from datetime import timedelta

    return (start_d, end_d + timedelta(days=1))


def _project_day_timeline_meta(
    *,
    refresh_id: str | None,
    start: str | None,
    end: str | None,
    project: str | None,
) -> dict[str, Any]:
    from lynchpin.mcp.tools._utils import best_materialized_refresh_id
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.views import ensure_views

    requested_end = _parse_date(end)
    requested_start = _parse_date(start)
    coverage_params: list[Any] = []
    coverage_clauses: list[str] = []
    selected_refresh_id = refresh_id
    with connect(substrate_path()) as conn:
        ensure_views(conn)
        if selected_refresh_id is None:
            selected_refresh_id = best_materialized_refresh_id(
                conn,
                "project_day_correlation",
                caller="lynchpin_evidence.timeline",
            )
        if selected_refresh_id is not None:
            coverage_clauses.append("refresh_id = ?")
            coverage_params.append(selected_refresh_id)
        if project is not None:
            coverage_clauses.append("project = ?")
            coverage_params.append(project)
        coverage_where = (
            " WHERE " + " AND ".join(coverage_clauses)
            if coverage_clauses
            else ""
        )
        first_date, last_date, coverage_count = conn.execute(
            f"SELECT MIN(date), MAX(date), COUNT(*) FROM project_day_correlation{coverage_where}",
            coverage_params,
        ).fetchone()

        match_params = list(coverage_params)
        match_clauses = list(coverage_clauses)
        if requested_start is not None:
            match_clauses.append("date >= ?")
            match_params.append(requested_start)
        if requested_end is not None:
            match_clauses.append("date <= ?")
            match_params.append(requested_end)
        match_where = " WHERE " + " AND ".join(match_clauses) if match_clauses else ""
        (matched_count,) = conn.execute(
            f"SELECT COUNT(*) FROM project_day_correlation{match_where}",
            match_params,
        ).fetchone()

    warning = None
    if requested_end is not None and last_date is not None and requested_end > last_date:
        warning = (
            "requested end exceeds materialized project-day correlation coverage; "
            "use lynchpin_project(action='commits') for live git"
        )
    elif matched_count == 0 and requested_end is not None:
        warning = "no materialized project-day correlation rows matched the requested window"

    return {
        "source_mode": "substrate",
        "refresh_id": selected_refresh_id,
        "coverage_start": first_date.isoformat() if first_date else None,
        "coverage_end": last_date.isoformat() if last_date else None,
        "coverage_row_count": int(coverage_count or 0),
        "matched_row_count": int(matched_count or 0),
        "freshness_warning": warning,
    }


def _compact_materialization_rows(rows: list[dict[str, Any]], *, names: tuple[str, ...]) -> list[dict[str, Any]]:
    wanted = set(names)
    compact: list[dict[str, Any]] = []
    for row in rows:
        if row.get("name") not in wanted:
            continue
        compact.append(
            {
                "name": row.get("name"),
                "status": row.get("status"),
                "reason": row.get("reason"),
                "row_count": (row.get("source_high_water") or {}).get("row_count"),
                "first_date": (row.get("source_high_water") or {}).get("first_date"),
                "last_date": (row.get("source_high_water") or {}).get("last_date"),
                "coverage": row.get("coverage"),
            }
        )
    return compact


def _situation_snapshot(start: str | None = None, end: str | None = None) -> dict[str, Any]:
    from lynchpin.materialization import audit_materialization
    from lynchpin.mcp.tools.git_analysis import repo_recent_commits
    from lynchpin.mcp.tools.runtime import mcp_runtime_status

    materialization_rows = [row.to_json() for row in audit_materialization()]
    projects = ("polylogue", "sinex", "sinity-lynchpin")
    commits = {
        project: repo_recent_commits(repo=project, limit=5)
        for project in projects
    }
    return {
        "kind": "situation_snapshot",
        "window": {"start": start, "end": end},
        "runtime": mcp_runtime_status(),
        "materialization": _compact_materialization_rows(
            materialization_rows,
            names=(
                "polylogue",
                "codex",
                "evidence_graph_substrate",
                "github_context",
                "activitywatch",
                "atuin",
                "machine",
                "raw_log",
                "communications",
                "the_motte",
                "code_snapshots",
            ),
        ),
        "recent_commits": commits,
        "caveats": [
            "recent_commits is live git; materialization rows are substrate/product coverage",
            "use detailed project/evidence tools for full row-level inspection",
        ],
    }


def _receipt_id(action: str) -> str:
    stamp = datetime.now(timezone.utc).isoformat()
    return f"mcp:{action}:{stamp}"


def _record_operation_receipt(
    *,
    action: str,
    execute: bool,
    reason: str,
    start: str | None = None,
    end: str | None = None,
    snapshot_refresh_id: str | None = None,
    artifact_paths: tuple[str, ...] = (),
    elapsed_ms: int = 0,
    caveats: tuple[str, ...] = (),
) -> str:
    from lynchpin.core.freshness import FreshnessReceipt, record_receipt

    rid = _receipt_id(action)
    record_receipt(
        FreshnessReceipt(
            receipt_id=rid,
            target=f"mcp_ops:{action}",
            decision="snapshot_enqueue" if execute else "cached_read",
            caller="lynchpin_ops",
            reason=reason,
            requested_start=start,
            requested_end=end,
            snapshot_refresh_id=snapshot_refresh_id,
            artifact_paths=artifact_paths,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=elapsed_ms,
            caveats=caveats,
        )
    )
    return rid


def _call(fn: Any, **kwargs: Any) -> Any:
    clean = {key: value for key, value in kwargs.items() if value is not None}
    signature = inspect.signature(fn)
    if not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        clean = {key: value for key, value in clean.items() if key in signature.parameters}
    return fn(**clean)


def _internal_call(module_name: str, function_name: str, **kwargs: Any) -> dict[str, Any]:
    import importlib

    tool_name = kwargs.pop("_tool_name", None)
    action = kwargs.pop("_action_name", None)
    extra_meta = kwargs.pop("_meta", None) or {}
    if tool_name is None or action is None:
        current = _CURRENT_ROUTE.get()
        if current is not None:
            tool_name, action = current
    module = importlib.import_module(module_name)
    fn = getattr(module, function_name)
    route = f"{module_name}.{function_name}"
    try:
        meta = {"route": route} if not tool_name or not action else _action_meta(str(tool_name), str(action), route=route)
        meta.update(extra_meta)
        return _ok(_call(fn, **kwargs), **meta)
    except Exception as exc:  # noqa: BLE001 - MCP boundary returns structured errors.
        return _error("tool_error", f"{type(exc).__name__}: {exc}", hint=f"route: {route}")


def _query_sql(sql: str, parameters: list[Any] | None = None, max_rows: int = 1000) -> dict[str, Any]:
    from lynchpin.mcp.tools.substrate import query_substrate

    return query_substrate(sql=sql, parameters=parameters, max_rows=max_rows)


_ENTITY_TABLES = {
    "commits": "commit_fact",
    "files": "file_change_fact",
    "ai_work": "ai_work_event",
    "github_issues": "github_issue",
    "github_prs": "github_pr",
    "evidence_nodes": "evidence_node",
    "evidence_edges": "evidence_edge",
    "claims": "analysis_claim",
    "personal_daily": "personal_daily_signal",
    "machine_metrics": "machine_metric_sample",
}


def _quote_ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"invalid identifier {value!r}")
    return '"' + value + '"'


def _query_dsl(spec: dict[str, Any]) -> dict[str, Any]:
    table = str(spec.get("table") or _ENTITY_TABLES.get(str(spec.get("entity") or "")) or "")
    if not table:
        return _error("missing_table", "spec requires table or known entity", choices=sorted(_ENTITY_TABLES))
    selected = spec.get("select") or ["*"]
    if selected == ["*"] or selected == "*":
        select_sql = "*"
    else:
        select_sql = ", ".join(_quote_ident(str(col)) for col in selected)
    sql = f"SELECT {select_sql} FROM {_quote_ident(table)}"
    params: list[Any] = []
    clauses: list[str] = []
    where = spec.get("where") or {}
    if not isinstance(where, dict):
        return _error("invalid_where", "where must be an object of column names to exact values")
    for key, value in where.items():
        clauses.append(f"{_quote_ident(str(key))} = ?")
        params.append(value)
    time_spec = spec.get("time") or {}
    if isinstance(time_spec, dict):
        column = str(time_spec.get("column") or "date")
        if time_spec.get("start") is not None:
            clauses.append(f"{_quote_ident(column)} >= ?")
            params.append(time_spec["start"])
        if time_spec.get("end") is not None:
            clauses.append(f"{_quote_ident(column)} <= ?")
            params.append(time_spec["end"])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    order_by = spec.get("order_by")
    if order_by:
        if isinstance(order_by, str):
            sql += f" ORDER BY {_quote_ident(order_by)}"
        elif isinstance(order_by, list):
            sql += " ORDER BY " + ", ".join(_quote_ident(str(col)) for col in order_by)
    limit = int(spec.get("limit") or 1000)
    sql += f" LIMIT {max(1, min(limit, 10_000))}"
    result = _query_sql(sql, params, max_rows=limit)
    if spec.get("explain"):
        result["sql"] = sql
        result["parameters"] = params
    return result


@app.tool()
def lynchpin_status(view: str = "runtime", start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Runtime/readiness/status router. view: runtime, readiness, self_check, materialization, operations, chisel, github."""
    if invalid := _mark_route("lynchpin_status", view):
        return invalid
    if view == "runtime":
        from lynchpin.mcp.tools.runtime import mcp_runtime_status

        return _ok(mcp_runtime_status(), **_action_meta("lynchpin_status", view, route="lynchpin.mcp.tools.runtime.mcp_runtime_status"))
    if view == "snapshot":
        return _ok(_situation_snapshot(start=start, end=end), **_action_meta("lynchpin_status", view, route="lynchpin.mcp.tools.public._situation_snapshot"))
    if view == "readiness":
        from lynchpin.mcp.tools.substrate import substrate_readiness_report

        return _ok(substrate_readiness_report(), **_action_meta("lynchpin_status", view, route="lynchpin.mcp.tools.substrate.substrate_readiness_report"))
    if view == "self_check":
        registered = set(_registered_public_tools())
        expected = set(PUBLIC_TOOL_NAMES)
        return _ok(
            {
                "registered_tool_count": len(registered),
                "expected_tool_count": len(expected),
                "registered_tools": sorted(registered),
                "missing_public_tools": sorted(expected - registered),
                "unexpected_tools": sorted(registered - expected),
                "metadata_tools": sorted(PUBLIC_TOOL_NAMES),
                "ok": registered == expected,
            },
            **_action_meta("lynchpin_status", view, route="lynchpin.mcp.tools.public.lynchpin_status"),
        )
    if view == "materialization":
        from lynchpin.materialization import audit_materialization

        return _ok([row.to_json() for row in audit_materialization()], **_action_meta("lynchpin_status", view, route="lynchpin.materialization.audit_materialization"))
    if view == "operations":
        from lynchpin.core.freshness import latest_receipts

        return _ok({"actions": _tool_actions("lynchpin_ops"), "receipts": latest_receipts(limit=20)}, **_action_meta("lynchpin_status", view, route="lynchpin.core.freshness.latest_receipts"))
    if view == "chisel":
        from lynchpin.mcp.tools.code_snapshots import code_snapshot_status

        return _ok(code_snapshot_status(), **_action_meta("lynchpin_status", view, route="lynchpin.mcp.tools.code_snapshots.code_snapshot_status"))
    if view == "github":
        from lynchpin.materialization import audit_materialization

        rows = [row.to_json() for row in audit_materialization() if row.name == "github_context"]
        return _ok(rows[0] if rows else {"status": "missing"}, **_action_meta("lynchpin_status", view, route="lynchpin.materialization.audit_materialization"))
    return _invalid_action("lynchpin_status", view)


@app.tool()
def lynchpin_catalog(
    domain: str | None = None,
    include_schema: bool = False,
) -> dict[str, Any]:
    """Catalog the collapsed MCP surface, actions, source routes, and query entities."""
    if invalid := _mark_route("lynchpin_catalog", "catalog"):
        return invalid
    tools = public_tool_catalog()
    if domain:
        tools = [tool for tool in tools if tool["group"] == domain or tool["name"] == domain]
    payload: dict[str, Any] = {
        "kind": "lynchpin_mcp_catalog",
        "tool_count": len(PUBLIC_TOOL_NAMES),
        "tools": tools,
        "domains": sorted({tool.group for tool in PUBLIC_TOOLS}),
    }
    if include_schema:
        from lynchpin.core.source_contracts import SOURCE_CONTRACTS

        payload["source_contracts"] = [
            {
                "name": contract.name,
                "collection_model": contract.collection_model,
                "materialization_mode": contract.materialization_mode,
                "substrate_tables": list(contract.substrate_tables),
                "graph_node_kinds": list(contract.graph_node_kinds),
            }
            for contract in SOURCE_CONTRACTS
        ]
        payload["query_entities"] = dict(sorted(_ENTITY_TABLES.items()))
    return _ok(payload, **_action_meta("lynchpin_catalog", "catalog", route="lynchpin.mcp.registry.public_tool_catalog"))


@app.tool()
def lynchpin_query(spec: dict[str, Any]) -> dict[str, Any]:
    """Read-only query surface. spec mode: dsl (default) or sql."""
    mode = str(spec.get("mode") or "dsl")
    if invalid := _mark_route("lynchpin_query", mode):
        return invalid
    try:
        if mode == "sql":
            return _ok(
                _query_sql(
                    sql=str(spec.get("sql") or ""),
                    parameters=spec.get("parameters"),
                    max_rows=int(spec.get("max_rows") or spec.get("limit") or 1000),
                ),
                **_action_meta("lynchpin_query", mode, route="lynchpin.mcp.tools.substrate.query_substrate", mode="sql"),
            )
        if mode == "dsl":
            result = _query_dsl(spec)
            return result if result.get("ok") is False else _ok(result, **_action_meta("lynchpin_query", mode, route="lynchpin.mcp.tools.public._query_dsl", mode="dsl"))
    except Exception as exc:  # noqa: BLE001 - MCP boundary returns structured errors.
        return _error("query_error", f"{type(exc).__name__}: {exc}")
    return _error("invalid_mode", f"unknown query mode {mode!r}", choices=("dsl", "sql"))


@app.tool()
def lynchpin_evidence(
    action: str = "graph",
    refresh_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    claim_id: str | None = None,
    limit: int = 100,
    start_id: str | None = None,
) -> dict[str, Any]:
    """Evidence router. action: graph, timeline, walk, claims, claim_evidence, coverage, confidence, crossref."""
    if invalid := _mark_route("lynchpin_evidence", action):
        return invalid
    if action == "graph":
        return _internal_call("lynchpin.mcp.tools.substrate", "evidence_graph", view="summary", refresh_id=refresh_id, start=start, end=end)
    if action == "timeline":
        timeline_meta = _project_day_timeline_meta(
            refresh_id=refresh_id,
            start=start,
            end=end,
            project=project,
        )
        return _internal_call(
            "lynchpin.mcp.tools.views",
            "project_day_correlations",
            refresh_id=refresh_id,
            start=start,
            end=end,
            projects=[project] if project else None,
            _meta=timeline_meta,
        )
    if action == "walk":
        if not start_id:
            return _error("missing_argument", "start_id is required for evidence walk")
        return _internal_call("lynchpin.mcp.tools.views", "walk_evidence", start_id=start_id, refresh_id=refresh_id, max_nodes=limit)
    if action == "claims":
        return _internal_call("lynchpin.mcp.tools.substrate", "analysis_evidence", view="claims", start=start, end=end, project=project, refresh_id=refresh_id, limit=limit)
    if action == "claim_evidence":
        if not claim_id:
            return _error("missing_argument", "claim_id is required for claim_evidence")
        return _internal_call("lynchpin.mcp.tools.substrate", "analysis_evidence", view="evidence", claim_id=claim_id, refresh_id=refresh_id, limit=limit)
    if action == "coverage":
        return _internal_call("lynchpin.mcp.tools.substrate", "contract_coverage", source=project, start=start, end=end)
    if action == "confidence":
        return _internal_call("lynchpin.mcp.tools.health", "substrate_confidence_matrix", refresh_id=refresh_id)
    if action == "crossref":
        if not start or not end:
            return _error("missing_argument", "start and end are required for crossref")
        return _internal_call("lynchpin.mcp.tools.views", "url_crossref", start=start, end=end, limit=limit)
    return _invalid_action("lynchpin_evidence", action)


@app.tool()
def lynchpin_project(
    action: str = "repos",
    repo: str | None = None,
    project: str | None = None,
    number: int | None = None,
    state: str | None = None,
    view: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Project router. action: repos, files, commits, velocity, hotspots, change_kinds, github, reviews, snapshots."""
    if invalid := _mark_route("lynchpin_project", action):
        return invalid
    target = repo or project
    if action == "repos":
        return _internal_call("lynchpin.mcp.tools.git_analysis", "repo_names")
    if action == "files":
        if not target:
            return _error("missing_argument", "repo or project is required for files")
        return _internal_call("lynchpin.mcp.tools.git_analysis", "repo_file_list", repo=target, limit=limit)
    if action == "commits":
        if not target:
            return _error("missing_argument", "repo or project is required for commits")
        return _internal_call(
            "lynchpin.mcp.tools.git_analysis",
            "repo_recent_commits",
            repo=target,
            limit=limit,
            _meta={"source_mode": "live_git"},
        )
    if action == "velocity":
        return _internal_call(
            "lynchpin.mcp.tools.velocity",
            "code_velocity",
            view=view or "throughput",
            project=target,
            start=start,
            end=end,
            _meta={"source_mode": "substrate"},
        )
    if action == "hotspots":
        return _internal_call("lynchpin.mcp.tools.change", "code_hotspots", view=view or "files", project=target, top_n=limit)
    if action == "change_kinds":
        return _internal_call("lynchpin.mcp.tools.change", "commit_analysis", view=view or "conventional", project=target)
    if action == "github":
        if number is not None and view == "issue":
            return _internal_call("lynchpin.mcp.tools.github", "get_github_issue", project=target, number=number)
        if number is not None:
            return _internal_call("lynchpin.mcp.tools.github", "get_github_pr", project=target, number=number)
        fn = "list_github_issues" if view == "issues" else "list_github_prs"
        return _internal_call(
            "lynchpin.mcp.tools.github",
            fn,
            project=target,
            state=state,
            limit=limit,
            _meta={"source_mode": "github_materialized"},
        )
    if action == "reviews":
        return _internal_call("lynchpin.mcp.tools.review", "review", view=view or "rows", projects=[target] if target else None)
    if action == "snapshots":
        return _internal_call("lynchpin.mcp.tools.code_snapshots", "code_snapshots", view=view or "status", project=target)
    return _invalid_action("lynchpin_project", action)


@app.tool()
def lynchpin_personal(
    action: str = "daily",
    view: str | None = None,
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    source: str | None = None,
    query: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Personal router. action: daily, activity, health, communications, web, bookmarks, media, operator, reports."""
    if invalid := _mark_route("lynchpin_personal", action):
        return invalid
    if action == "daily":
        return _internal_call("lynchpin.mcp.tools.personal", "personal_daily_signals", start=start, end=end, source=source, limit=limit)
    if action == "activity":
        if view == "focus":
            return _internal_call("lynchpin.mcp.tools.personal", "focus_daily", start=start, end=end)
        return _internal_call("lynchpin.mcp.tools.personal", "activity_content", view=view or "daily", start=start, end=end, limit=limit)
    if action == "health":
        fn = {
            "daily": "health_daily_summary",
            "stress": "health_stress_detail",
            "heart_rate": "health_heart_rate_detail",
            "hrv": "health_hrv_trend",
        }.get(view or "trend", "health_trend")
        return _internal_call("lynchpin.mcp.tools.health", fn, start=start, end=end)
    if action == "communications":
        return _internal_call("lynchpin.mcp.tools.personal", "communication", view=view or "events", start=start, end=end, limit=limit)
    if action == "web":
        if view == "takeout":
            return _internal_call("lynchpin.mcp.tools.personal", "google_takeout", view="events", start=start, end=end, query=query, limit=limit)
        return _internal_call("lynchpin.mcp.tools.personal", "web", view=view or "daily", start=start, end=end)
    if action == "bookmarks":
        return _internal_call("lynchpin.mcp.tools.personal", "bookmarks", view=view or "search", query=query, start=start, end=end, limit=limit)
    if action == "media":
        return _internal_call("lynchpin.mcp.tools.personal", "spotify_daily", start=start, end=end)
    if action == "operator":
        return _internal_call("lynchpin.mcp.tools.personal", "operator", view=view or "rhythm", start=start or "", end=end or "", project=project)
    if action == "reports":
        report = view or "anomaly"
        mapping = {
            "anomaly": ("lynchpin.mcp.tools.personal_analysis", "anomaly_crossref_report"),
            "life_phase": ("lynchpin.mcp.tools.personal_analysis", "life_phase_report"),
            "productivity": ("lynchpin.mcp.tools.personal_analysis", "productivity_predictors_report"),
            "substance": ("lynchpin.mcp.tools.personal_analysis", "substance_health_report"),
            "burnout": ("lynchpin.mcp.tools.personal_analysis", "burnout_warning_report"),
            "ai_efficiency": ("lynchpin.mcp.tools.personal_analysis", "ai_session_efficiency_report"),
        }
        if report not in mapping:
            return _error("invalid_report", f"unknown report {report!r}", choices=sorted(mapping))
        module, fn = mapping[report]
        return _internal_call(module, fn, project=project)
    return _invalid_action("lynchpin_personal", action)


@app.tool()
def lynchpin_machine(
    action: str = "status",
    view: str | None = None,
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    host: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Machine router. action: status, metrics, pressure, services, workloads, observations, benchmarks, diagnostics, windows."""
    if invalid := _mark_route("lynchpin_machine", action):
        return invalid
    if action == "status":
        if view == "materialization":
            return _internal_call("lynchpin.mcp.tools.machine_status", "machine_materialization_health")
        return _internal_call("lynchpin.mcp.tools.machine_status", "machine_status")
    if action == "metrics":
        return _internal_call("lynchpin.mcp.tools.machine_status", "machine_metrics", by=view or "daily", start=start, end=end, host=host)
    if action == "pressure":
        fn = "machine_pressure_explain" if view == "explain" else "machine_pressure_report"
        return _internal_call("lynchpin.mcp.tools.machine_status", fn, start=start, end=end, host=host, limit=limit)
    if action == "services":
        return _internal_call("lynchpin.mcp.tools.machine_status", "machine_service", view=view or "state_summary", start=start, end=end, host=host, limit=limit)
    if action == "workloads":
        mapping = {
            "summary": "machine_workload_summary",
            "sessions": "machine_agent_sessions",
            "co_presence": "machine_co_presence",
            "scope": "machine_scope_timeline",
            "heatmap": "machine_hourly_heatmap",
            "orphans": "machine_orphan_processes",
        }
        fn = mapping.get(view or "summary", "machine_workload_summary")
        return _internal_call("lynchpin.mcp.tools.machine_workloads", fn, start=start, end=end)
    if action == "observations":
        return _internal_call("lynchpin.mcp.tools.machine_observations", "machine_work_observations", view=view or "daily", start=start, end=end, project=project, limit=limit)
    if action == "benchmarks":
        return _internal_call("lynchpin.mcp.tools.machine_benchmarks", "machine_benchmarks", view=view or "runs", limit=limit)
    if action == "diagnostics":
        return _internal_call("lynchpin.mcp.tools.machine_diagnostics", "machine_attribution", view=view or "summary", project=project, limit=limit)
    if action == "windows":
        return _internal_call("lynchpin.mcp.tools.machine_status", "machine_windows", view=view or "context", start=start, end=end, project=project, limit=limit)
    return _invalid_action("lynchpin_machine", action)


@app.tool()
def lynchpin_ops(
    action: str = "materialize",
    execute: bool = False,
    source: str | None = None,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
    refresh_id: str | None = None,
    limit: int = 20,
    title: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Operations router. action: materialize, github_refresh, chisel, ai_backfill, promote_artifact, prune, receipt."""
    if invalid := _mark_route("lynchpin_ops", action):
        return invalid
    started = datetime.now(timezone.utc)
    try:
        if action == "receipt":
            from lynchpin.core.freshness import latest_receipts

            return _ok(latest_receipts(limit=limit, target="mcp_ops:" + source if source else None), **_current_meta(route="lynchpin.core.freshness.latest_receipts"))
        if action == "materialize":
            from lynchpin.materialization import ensure_materialized, plan_materializations

            if not execute:
                if source:
                    result = ensure_materialized(source, window=_date_window(start, end), budget="manual", force=force)
                    return _ok({"dry_run": True, "result": result.to_json()}, **_current_meta(route="lynchpin.materialization.ensure_materialized"))
                return _ok({"dry_run": True, "plan": [step.to_json() for step in plan_materializations(force=force)]}, **_current_meta(route="lynchpin.materialization.plan_materializations"))
            if not source:
                return _error("missing_argument", "source is required when executing materialize")
            result = ensure_materialized(source, window=_date_window(start, end), force=force)
            rid = _record_operation_receipt(action=action, execute=True, reason=result.reason, start=start, end=end)
            return _ok({"dry_run": False, "receipt_id": rid, "result": result.to_json()}, **_current_meta(route="lynchpin.materialization.ensure_materialized"))
        if action == "github_refresh":
            if not execute:
                return _ok({"dry_run": True, "projects": [source] if source else None}, **_current_meta(route="lynchpin.ingest.github_context_materialize.materialize_github_context"))
            from lynchpin.ingest.github_context_materialize import materialize_github_context

            report = materialize_github_context(projects={source} if source else None)
            rid = _record_operation_receipt(action=action, execute=True, reason="github context refreshed", start=start, end=end)
            return _ok({"dry_run": False, "receipt_id": rid, "report": report}, **_current_meta(route="lynchpin.ingest.github_context_materialize.materialize_github_context"))
        if action == "chisel":
            if not execute:
                from lynchpin.mcp.tools.code_snapshots import code_snapshot_status

                return _ok({"dry_run": True, "status": code_snapshot_status()}, **_current_meta(route="lynchpin.mcp.tools.code_snapshots.code_snapshot_status"))
            from lynchpin.sources.chisel import build_chisel_bundles

            result = build_chisel_bundles(projects=source or "")
            rid = _record_operation_receipt(action=action, execute=True, reason="chisel snapshots generated")
            return _ok({"dry_run": False, "receipt_id": rid, "result": result}, **_current_meta(route="lynchpin.sources.chisel.build_chisel_bundles"))
        if action == "ai_backfill":
            from lynchpin.mcp.tools.substrate import ai_attribution_backfill

            result = ai_attribution_backfill(refresh_id=refresh_id, dry_run=not execute)
            rid = None if not execute else _record_operation_receipt(action=action, execute=True, reason="ai attribution backfilled", snapshot_refresh_id=refresh_id)
            return _ok({"dry_run": not execute, "receipt_id": rid, "result": result}, **_current_meta(route="lynchpin.mcp.tools.substrate.ai_attribution_backfill"))
        if action == "promote_artifact":
            if not title or not path:
                return _error("missing_argument", "title and path are required for promote_artifact")
            from lynchpin.mcp.tools.health import promote_analysis_product

            result = promote_analysis_product(title=title, path=path, refresh_id=refresh_id, dry_run=not execute)
            rid = None if not execute else _record_operation_receipt(action=action, execute=True, reason="analysis product promoted", snapshot_refresh_id=refresh_id, artifact_paths=(path,))
            return _ok({"dry_run": not execute, "receipt_id": rid, "result": result}, **_current_meta(route="lynchpin.mcp.tools.health.promote_analysis_product"))
        if action == "prune":
            from lynchpin.mcp.tools.substrate import substrate_prune

            result = substrate_prune(keep_builds=max(1, limit), dry_run=not execute)
            rid = None if not execute else _record_operation_receipt(action=action, execute=True, reason="substrate pruned")
            return _ok({"dry_run": not execute, "receipt_id": rid, "result": result}, **_current_meta(route="lynchpin.mcp.tools.substrate.substrate_prune"))
    except Exception as exc:  # noqa: BLE001 - MCP operation boundary returns structured errors.
        elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return _error("operation_error", f"{type(exc).__name__}: {exc}", hint=f"elapsed_ms={elapsed}")
    return _invalid_action("lynchpin_ops", action)


def _registered_public_tools() -> tuple[str, ...]:
    tools = getattr(getattr(app, "_tool_manager", None), "_tools", {})
    if not isinstance(tools, dict):
        return ()
    return tuple(sorted(str(name) for name in tools))


def _tool_actions(tool_name: str) -> list[dict[str, Any]]:
    for tool in public_tool_catalog():
        if tool["name"] == tool_name:
            return list(tool["actions"])
    return []
