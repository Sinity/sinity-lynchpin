"""Per-source MCP capability matrix.

Provides ``mcp_capability_matrix`` — for every known Lynchpin source, return
its canonical product, substrate table(s), graph node kinds, MCP tools,
freshness, materialization status, and known caveats. Call this before
designing a new analysis or when unsure whether a question can be answered
through current MCP exposure.

This is a per-source view; ``mcp_capability_map`` (in substrate.py) is a
per-tool view of analytic MCP tools and their backing contracts. They are
complementary.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from datetime import date as _date_type
from typing import Any

from lynchpin.mcp.server import app


# Static per-source enrichment that cannot be derived purely from
# SOURCE_CONTRACTS / audit_materialization (substrate-table mapping, graph
# node kinds, MCP tool list, caveats). The intent is to be discoverable; this
# table is read by mcp_capability_matrix() and merged with live audit data.
_SOURCE_ENRICHMENT: dict[str, dict[str, Any]] = {
    "webhistory": {
        "substrate_tables": [],
        "graph_node_kinds": ["web_domain_day"],
        "mcp_tools": ["web_daily"],
        "caveats": [
            "graph layer emits only daily domain aggregates (no per-visit nodes)",
            "weak_* buckets in web_daily are host/path matches, not semantic classification",
        ],
    },
    "google_takeout": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["google_activity_day"],
        "mcp_tools": ["google_takeout_daily", "google_takeout_events", "personal_daily_signals"],
        "caveats": [
            "typed activity/event surface excludes contacts and asset inventories",
            "YouTube subscription/export rows without timestamps are inventory, not activity",
        ],
    },
    "polylogue": {
        "substrate_tables": [],
        "graph_node_kinds": ["ai_session", "ai_work_event"],
        "mcp_tools": [
            "(via polylogue MCP server: session_profiles, day_session_summaries, "
            "session_work_events, ...)",
        ],
        "caveats": [
            "deep polylogue analytics live on the polylogue MCP server, not lynchpin",
        ],
    },
    "activitywatch": {
        "substrate_tables": ["activity_content_day", "activity_content_bucket", "activity_title_usage"],
        "graph_node_kinds": [
            "focus_span", "focus_day", "deep_work_block", "focus_loop",
            "attention_day", "circadian_profile", "fragmentation_day", "activity_content_day",
        ],
        "mcp_tools": ["focus_daily", "activity_content_daily", "activity_title_usage", "activity_unmatched_titles"],
        "caveats": [
            "rich source API but no dedicated substrate table; canonical NDJSON is the surface",
        ],
    },
    "title_metadata": {
        "substrate_tables": ["title_classification"],
        "graph_node_kinds": [],
        "mcp_tools": ["title_metadata_status", "activity_title_usage", "activity_unmatched_titles"],
        "caveats": [
            "canonical historical GPT/rules classifications; no new classifier runs in this phase",
        ],
    },
    "activity_content": {
        "substrate_tables": ["activity_content_day", "activity_content_bucket", "activity_title_usage"],
        "graph_node_kinds": ["activity_content_day"],
        "mcp_tools": ["activity_content_daily", "activity_content_coverage", "activity_title_usage", "activity_unmatched_titles"],
        "caveats": [
            "coverage is bounded by title classification matches; unmatched title table is the audit surface",
        ],
    },
    "atuin": {
        "substrate_tables": [],
        "graph_node_kinds": ["terminal_session", "terminal_pattern"],
        "mcp_tools": [],
        "caveats": [
            "terminal/atuin: canonical NDJSON exists but no dedicated MCP tool surface",
        ],
    },
    "evidence_graph_substrate": {
        "substrate_tables": [
            "commit_fact", "file_change_fact", "symbol_change", "ai_work_event",
            "pr_review_row", "evidence_graph_build", "evidence_node",
            "evidence_edge", "analysis_claim", "substrate_promotion_run",
            "substrate_source_status",
        ],
        "graph_node_kinds": ["commit", "ai_work_event", "ai_session", "analysis_claim"],
        "mcp_tools": [
            "query_substrate", "list_substrate_tables", "substrate_readiness_report",
            "substrate_source_status", "load_evidence_graph_summary",
            "list_evidence_graph_builds", "project_day_correlations",
            "closure_chain_walks", "file_overlap_edges", "symbol_overlap_edges",
            "analysis_claims", "claim_evidence", "promotion_runs",
        ],
        "caveats": [
            "evidence_graph_substrate is itself a stage; depends on every source's promotion run",
            "no materialized graph build implies summary tools may raise unpromoted-data errors",
        ],
    },
    "health": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["health_metric"],
        "mcp_tools": ["personal_daily_signals", "health_trend"],
        "caveats": ["Samsung Health export tends to lag; check last_date for staleness"],
    },
    "sleep": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["sleep_quality", "readiness_forecast"],
        "mcp_tools": ["personal_daily_signals"],
        "caveats": ["depends on Sleep-as-Android / Samsung Health export refresh"],
    },
    "substance": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": [],
        "mcp_tools": ["personal_daily_signals"],
        "caveats": ["manually edited CSV at /realm/data/exports/health/processed/"],
    },
    "spotify": {
        "substrate_tables": ["spotify_daily"],
        "graph_node_kinds": ["listening_session"],
        "mcp_tools": ["spotify_daily", "personal_daily_signals"],
        "caveats": [
            "graph integration omitted when no materialized graph is available",
            "needs GDPR export refresh to advance last_date",
        ],
    },
    "reddit": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": [],
        "mcp_tools": ["personal_daily_signals"],
        "caveats": ["GDPR export-based; refresh requires re-export from reddit.com"],
    },
    "facebook_messenger": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["communication_activity"],
        "mcp_tools": ["communication_events", "communication_daily"],
        "caveats": ["GDPR export only; superseded for unified access by 'communications'"],
    },
    "communications": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["communication_activity"],
        "mcp_tools": ["communication_events", "communication_daily"],
        "caveats": [
            "covers Messenger plus parseable Outlook exports; Teams/IRC not yet typed",
        ],
    },
    "raindrop": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["bookmark_activity"],
        "mcp_tools": ["bookmarks_search", "bookmark_daily"],
        "caveats": ["no domain/topic join with webhistory yet"],
    },
    "browser_bookmarks": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["bookmark_activity"],
        "mcp_tools": ["bookmarks_search", "bookmark_daily"],
        "caveats": [
            "covers Chromium bookmarks plus exported Firefox/Vivaldi; dedup with Raindrop is coarse",
        ],
    },
    "arbtt": {
        "substrate_tables": ["personal_daily_signal"],
        "graph_node_kinds": ["arbtt_focus_activity"],
        "mcp_tools": ["focus_daily"],
        "caveats": ["historical focus source; weaker title/category attribution than ActivityWatch"],
    },
    "machine": {
        "substrate_tables": [
            "machine_metric_sample", "machine_gpu_sample",
            "machine_service_state", "machine_network_sample",
            "machine_experiment_run",
        ],
        "graph_node_kinds": [],
        "mcp_tools": [
            "machine_metrics_daily", "machine_episodes", "machine_context_windows",
            "machine_below_attributions", "machine_observational_baselines",
            "machine_experiment_claims", "machine_service_state_summary",
            "machine_gap_summary", "machine_bufferbloat_summary",
            "borg_drill_history", "sinnix_generation_history",
        ],
        "caveats": ["telemetry SQLite/JSONL; very advanced and coherent surface"],
    },
    "spotify_daily": {
        "substrate_tables": ["spotify_daily"],
        "graph_node_kinds": [],
        "mcp_tools": ["spotify_daily", "derived_product_status"],
        "caveats": ["derived from canonical Spotify streams; substrate promotion copies this product"],
    },
    "personal_daily_signals": {
        "substrate_tables": ["personal_daily_signal", "activity_content_day", "activity_content_bucket", "activity_title_usage"],
        "graph_node_kinds": ["health_metric", "sleep_quality", "communication_activity", "bookmark_activity", "activity_content_day"],
        "mcp_tools": ["personal_daily_signals", "derived_product_status"],
        "caveats": ["derived from canonical products; substrate promotion copies this product"],
    },
}


def _freshness(last_date: _date_type | None, today: _date_type) -> dict[str, Any]:
    if last_date is None:
        return {"last_date": None, "days_stale": None, "status": "missing"}
    delta = (today - last_date).days
    if delta < 0:
        # last_date in the future — treat as live
        status = "live"
    elif delta <= 2:
        status = "live"
    elif delta <= 7:
        status = "near"
    elif delta <= 30:
        status = "stale"
    else:
        status = "very_stale"
    return {"last_date": last_date.isoformat(), "days_stale": delta, "status": status}


def _materialization_status_label(status: str) -> str:
    if status == "ready":
        return "ready"
    if status in {"partial", "stale", "degraded"}:
        return "partial"
    if status in {"missing", "empty"}:
        return "missing"
    if status == "error":
        return "blocked"
    return status


@app.tool()
def mcp_capability_matrix() -> list[dict[str, Any]]:
    """Per-source capability matrix for the Lynchpin MCP surface.

    Returns one row per known source with: canonical product (raw authority
    + materialized paths), substrate table(s), graph node kinds, MCP tool
    names, freshness (last_date / days_stale / status), materialization
    status (ready/partial/blocked/missing), and known caveats.

    Call this before designing a new analysis, or when an agent needs to
    decide whether to query MCP, drop to ``query_substrate``, or shell out to
    raw CLI.
    """
    from lynchpin.core.source_contracts import SOURCE_CONTRACTS
    from lynchpin.materialization import audit_materialization

    today = _date_type.today()
    audit_by_name = {row.name: row for row in audit_materialization()}

    rows: list[dict[str, Any]] = []
    for contract in SOURCE_CONTRACTS:
        name = contract.name
        audit = audit_by_name.get(name)
        enrichment = _SOURCE_ENRICHMENT.get(name, {})

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
                "raw_authority": raw_authority,
                "materialized_product": materialized_product,
                "materialized_paths": materialized_paths,
                "row_count": row_count,
                "first_date": first_date.isoformat() if first_date else None,
                "freshness": _freshness(last_date, today),
                "materialization_status": _materialization_status_label(status),
                "raw_status": status,
                "substrate_table": list(enrichment.get("substrate_tables", [])),
                "graph_node_kinds": list(enrichment.get("graph_node_kinds", [])),
                "mcp_tools": list(enrichment.get("mcp_tools", [])),
                "query_surface": contract.query_surface,
                "refresh_command": contract.refresh_command,
                "substrate_daily_signal": contract.substrate_daily_signal,
                "caveats": list(enrichment.get("caveats", []))
                + ([audit.reason] if audit and audit.reason else []),
            }
        )
    return rows


__all__ = ["mcp_capability_matrix"]
