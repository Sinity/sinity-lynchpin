"""Per-source MCP capability matrix.

Provides ``mcp_capability_matrix`` — for every known Lynchpin source, return
its canonical product, substrate table(s), graph node kinds, MCP tools,
date coverage, materialization status, and known caveats. Call this before
designing a new analysis or when unsure whether a question can be answered
through current MCP exposure.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app


_PRIMARY_TOOL_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "group": "orientation",
        "use_when": "Decide which Lynchpin surface to query, check runtime/materialization health, or inspect generated artifacts.",
        "primary_tools": (
            "mcp_guide",
            "mcp_capability_matrix",
            "mcp_status",
            "observability_status",
            "materialization_status",
            "analysis_artifact_inventory",
            "read_analysis_artifact",
            "code_snapshots",
        ),
    },
    {
        "group": "substrate_and_evidence",
        "use_when": "Ask graph/substrate questions where provenance, refresh IDs, or SQL-backed joins matter.",
        "primary_tools": (
            "substrate_health",
            "evidence_graph",
            "analysis_evidence",
            "project_day_correlations",
            "walk_evidence",
            "overlap_edges",
            "closure_chain_walks",
            "query_substrate",
        ),
    },
    {
        "group": "code_and_project_work",
        "use_when": "Inspect repository activity, code snapshots, GitHub lifecycle, velocity, churn, reviews, and hotspots.",
        "primary_tools": (
            "code_snapshots",
            "repo_names",
            "repo_recent_commits",
            "repo_file_list",
            "velocity",
            "code_velocity",
            "code_hotspots",
            "commit_analysis",
            "review",
            "list_github_issues",
            "get_github_issue",
            "list_github_prs",
            "get_github_pr",
        ),
    },
    {
        "group": "personal_signals",
        "use_when": "Read operator/day signals and human activity sources without dropping to raw source modules.",
        "primary_tools": (
            "personal_daily_signals",
            "operator",
            "rhythm",
            "activity_content",
            "focus_daily",
            "terminal",
            "web",
            "google_takeout",
            "communication",
            "bookmarks",
            "keylog",
            "spotify_daily",
            "health_trend",
        ),
    },
    {
        "group": "machine_and_runtime",
        "use_when": "Inspect local machine telemetry, pressure, services, experiments, validation designs, and work observations.",
        "primary_tools": (
            "machine_status",
            "machine_metrics",
            "machine_pressure_report",
            "machine_pressure_explain",
            "machine_service",
            "machine_windows",
            "machine_episodes",
            "machine_below",
            "machine_observational",
            "machine_work_observations",
            "machine_benchmarks",
            "machine_validation_design",
            "machine_gaps",
        ),
    },
)


def _materialization_status_label(status: str) -> str:
    if status == "ready":
        return "ready"
    if status in {"partial", "degraded"}:
        return "partial"
    if status in {"missing", "empty"}:
        return "missing"
    if status == "error":
        return "blocked"
    return status


def _tool_doc(name: str) -> str | None:
    import lynchpin.mcp.server as server

    tools = getattr(getattr(server.app, "_tool_manager", None), "_tools", {})
    tool = tools.get(name) if isinstance(tools, dict) else None
    if tool is None:
        return None
    description = getattr(tool, "description", None)
    if not description:
        description = getattr(tool, "fn", None).__doc__ if getattr(tool, "fn", None) else None
    if not description:
        return None
    return " ".join(str(description).strip().split())


def _tool_dispatch_parameter(name: str) -> dict[str, Any] | None:
    import inspect
    import re

    import lynchpin.mcp.server as server

    tools = getattr(getattr(server.app, "_tool_manager", None), "_tools", {})
    tool = tools.get(name) if isinstance(tools, dict) else None
    fn = getattr(tool, "fn", None)
    if fn is None:
        return None
    signature = inspect.signature(fn)
    parameter_name = None
    for candidate in ("view", "by"):
        if candidate in signature.parameters:
            parameter_name = candidate
            break
    if parameter_name is None:
        return None

    parameter = signature.parameters[parameter_name]
    default = None if parameter.default is inspect.Parameter.empty else parameter.default
    text_parts = [inspect.getdoc(fn) or ""]
    try:
        text_parts.append(inspect.getsource(fn))
    except OSError:
        pass
    text = "\n".join(text_parts)
    choices: list[str] = []
    match = re.search(r"choices:\s*([^\"}\n]+)", text)
    if match:
        choices = [
            choice.strip().strip(".,")
            for choice in match.group(1).split(",")
            if choice.strip()
        ]
    if not choices:
        quoted = re.findall(rf'{parameter_name}\s*==\s*["\']([^"\']+)["\']', text)
        choices = sorted(set(quoted))
    if default is not None and str(default) not in choices:
        choices.insert(0, str(default))
    return {
        "parameter": parameter_name,
        "default": default,
        "choices": choices,
    }


@app.tool()
def mcp_guide() -> dict[str, Any]:
    """Compact routing guide for the Lynchpin MCP surface.

    Use this before broad exploration. It summarizes the small set of primary
    entry points, the registered tool count, contract drift, and source-to-tool
    routes without returning the full per-source capability matrix.
    """
    from lynchpin.core.source_contracts import SOURCE_CONTRACTS
    from lynchpin.mcp.tools._utils import mcp_tool_registry_summary

    registry = mcp_tool_registry_summary()
    registered = set(registry["registered"])
    declared = set(registry["declared"])
    platform = set(registry["platform"])
    primary = {
        tool
        for group in _PRIMARY_TOOL_GROUPS
        for tool in group["primary_tools"]
    }

    groups: list[dict[str, Any]] = []
    for group in _PRIMARY_TOOL_GROUPS:
        tools = []
        for tool in group["primary_tools"]:
            tools.append(
                {
                    "name": tool,
                    "registered": tool in registered,
                    "declared_by_contract": tool in declared,
                    "platform_tool": tool in platform,
                    "description": _tool_doc(tool),
                    "dispatch": _tool_dispatch_parameter(tool),
                }
            )
        groups.append(
            {
                "group": group["group"],
                "use_when": group["use_when"],
                "tools": tools,
            }
        )

    source_routes = []
    for contract in SOURCE_CONTRACTS:
        if not contract.mcp_tools:
            continue
        source_routes.append(
            {
                "source": contract.name,
                "collection_model": contract.collection_model,
                "materialization_mode": contract.materialization_mode,
                "primary_tools": [
                    tool
                    for tool in contract.mcp_tools
                    if not tool.startswith("(") and tool in primary
                ],
                "all_declared_tools": [
                    tool for tool in contract.mcp_tools if not tool.startswith("(")
                ],
                "external_tools": [
                    tool for tool in contract.mcp_tools if tool.startswith("(")
                ],
                "caveats": list(contract.caveats),
            }
        )

    return {
        "kind": "lynchpin_mcp_guide",
        "registered_tool_count": len(registered),
        "declared_tool_count": len(declared),
        "platform_tool_count": len(platform),
        "primary_tool_count": len(primary),
        "missing_primary_tools": sorted(primary - registered),
        "missing_declared_tools": list(registry["missing_declared"]),
        "missing_platform_tools": list(registry["missing_platform"]),
        "registered_platform_tools": list(registry["registered_platform"]),
        "unexpected_unmapped_tool_count": len(registry["unexpected_unmapped"]),
        "unexpected_unmapped_tool_sample": list(registry["unexpected_unmapped"])[:20],
        "registered_unmapped_tool_count": len(registry["registered_unmapped"]),
        "registered_unmapped_tool_sample": list(registry["registered_unmapped"])[:20],
        "largest_modules": [
            {
                "module": row["module"],
                "registered_tool_count": row["registered_tool_count"],
                "declared_tool_count": row["declared_tool_count"],
                "platform_tool_count": row["platform_tool_count"],
                "unexpected_unmapped_tool_count": row["unexpected_unmapped_tool_count"],
                "tool_sample": list(row["tools"])[:10],
            }
            for row in list(registry["module_summary"])[:8]
        ],
        "groups": groups,
        "source_routes": source_routes,
        "usage_notes": [
            "Start with mcp_guide for routing and mcp_capability_matrix for per-source detail.",
            "Prefer aggregate tools such as substrate_health, evidence_graph, code_snapshots, operator, machine_metrics, machine_service, and activity_content before direct SQL.",
            "Use query_substrate when an aggregate tool does not expose the required join; it is SELECT-only.",
            "Respect collection_model and materialization_mode before interpreting missing rows as zero activity.",
            "Use largest_modules to see which implementation modules dominate the exposed tool surface.",
            "Platform tools are intentionally not source-contract routes; use unexpected_unmapped_tool_count for real routing drift.",
            "Use mcp_status(view='self_check') when you need the full registry classification.",
        ],
    }


@app.tool()
def mcp_capability_matrix() -> list[dict[str, Any]]:
    """Per-source capability matrix for the Lynchpin MCP surface.

    Returns one row per known source with: canonical product (raw authority
    + materialized paths), substrate table(s), graph node kinds, MCP tool
    names, date bounds, requested-window coverage semantics when available,
    materialization status (ready/partial/blocked/missing), and known caveats.

    Call this before designing a new analysis, or when an agent needs to
    decide whether to query MCP, drop to ``query_substrate``, or shell out to
    raw CLI.
    """
    from lynchpin.core.config import get_config
    from lynchpin.core.source_contracts import SOURCE_CONTRACT_ALIASES, SOURCE_CONTRACTS
    from lynchpin.materialization import audit_materialization

    audit_by_name = {row.name: row for row in audit_materialization()}
    available_sources = get_config().available_sources()
    aliases_by_contract: dict[str, list[str]] = {}
    for source_key, contract_name in SOURCE_CONTRACT_ALIASES.items():
        aliases_by_contract.setdefault(contract_name, []).append(source_key)

    rows: list[dict[str, Any]] = []
    for contract in SOURCE_CONTRACTS:
        name = contract.name
        audit = audit_by_name.get(name)
        source_keys = [name, *sorted(aliases_by_contract.get(name, []))]
        materialized_paths = (
            [str(p) for p in audit.materialized_paths] if audit else []
        )
        raw_roots = [str(p) for p in audit.raw_roots] if audit else []

        materialized_product = materialized_paths[0] if materialized_paths else None
        raw_authority = raw_roots[0] if raw_roots else contract.authority

        last_date = audit.last_date if audit else None
        first_date = audit.first_date if audit else None
        row_count = audit.row_count if audit else None
        status = audit.status if audit else "missing"

        rows.append(
            {
                "source": name,
                "source_keys": source_keys,
                "source_available": any(available_sources.get(key, False) for key in source_keys),
                "raw_authority": raw_authority,
                "materialized_product": materialized_product,
                "materialized_paths": materialized_paths,
                "row_count": row_count,
                "date_bounds": {
                    "first_date": first_date.isoformat() if first_date else None,
                    "last_date": last_date.isoformat() if last_date else None,
                },
                "first_date": first_date.isoformat() if first_date else None,
                "last_date": last_date.isoformat() if last_date else None,
                "materialization_status": _materialization_status_label(status),
                "raw_status": status,
                "collection_model": contract.collection_model,
                "materialization_mode": contract.materialization_mode,
                "materialization_target": contract.materialization_target,
                "default_max_age_seconds": contract.default_max_age_seconds,
                "coverage": audit.to_json()["coverage"] if audit else None,
                "substrate_table": list(contract.substrate_tables),
                "graph_node_kinds": list(contract.graph_node_kinds),
                "mcp_tools": list(contract.mcp_tools),
                "query_surface": contract.query_surface,
                "materialization_hint": contract.materialization_hint,
                "materialization_executor": contract.materialization_executor.to_json(),
                "substrate_daily_signal": contract.substrate_daily_signal,
                "caveats": list(contract.caveats)
                + ([audit.reason] if audit and audit.reason else []),
            }
        )
    return rows


__all__ = ["mcp_capability_matrix"]
