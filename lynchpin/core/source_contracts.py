"""Canonical contracts for Lynchpin datasets and substrate stages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

SourceEmptiness = Literal["valid", "degraded", "invalid"]
DatasetStatus = Literal["ready", "empty", "missing", "partial", "degraded", "error"]
SubstrateStatus = Literal["ok", "empty", "unavailable", "error"]
ContractKind = Literal["dataset", "stage"]
QueryMode = Literal["canonical", "substrate", "live"]
CollectionModel = Literal["continuous", "event_export", "derived", "metadata", "historical", "stage"]


@dataclass(frozen=True)
class SourceContract:
    name: str
    authority: str
    query_surface: str
    refresh_command: str
    required: bool = True
    empty: SourceEmptiness = "invalid"
    substrate_daily_signal: bool = False
    kind: ContractKind = "dataset"
    query_mode: QueryMode = "canonical"
    collection_model: CollectionModel = "event_export"
    substrate_tables: tuple[str, ...] = ()
    graph_node_kinds: tuple[str, ...] = ()
    mcp_tools: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageContract:
    name: str
    query_surface: str
    required: bool = True
    kind: ContractKind = "stage"


SOURCE_CONTRACTS: tuple[SourceContract, ...] = (
    SourceContract(
        name="webhistory",
        authority="all canonical webhistory segment files plus raw Takeout archives",
        query_surface="lynchpin.sources.web",
        refresh_command="python -m lynchpin.ingest.webhistory",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="google_takeout",
        authority="raw Google Takeout archives",
        query_surface="lynchpin.sources.google_takeout plus lynchpin.sources.google_takeout_products",
        refresh_command="python -m lynchpin.ingest.google_takeout_materialize && python -m lynchpin.ingest.google_takeout_products",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="polylogue",
        authority="Polylogue archive database",
        query_surface="lynchpin.sources.polylogue",
        refresh_command="polylogue doctor --repair --target session_insights",
    ),
    SourceContract(
        name="activitywatch",
        authority="ActivityWatch live SQLite plus exported backup DBs",
        query_surface="lynchpin.sources.activitywatch",
        refresh_command="python -m lynchpin.ingest.activitywatch_materialize",
    ),
    SourceContract(
        name="title_metadata",
        authority="historical GPT/rules title classification DuckDB",
        query_surface="lynchpin.sources.title_metadata",
        refresh_command="python -m lynchpin.ingest.title_metadata_materialize",
        substrate_daily_signal=False,
    ),
    SourceContract(
        name="activity_content",
        authority="canonical ActivityWatch events joined to canonical title metadata",
        query_surface="lynchpin.sources.activity_content.iter_activity_content_days",
        refresh_command="python -m lynchpin.ingest.activity_content_materialize",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="atuin",
        authority="Atuin live SQLite",
        query_surface="lynchpin.sources.terminal",
        refresh_command="python -m lynchpin.ingest.terminal_materialize",
    ),
    SourceContract(
        name="evidence_graph_substrate",
        authority="source modules promoted into DuckDB",
        query_surface="lynchpin.graph.context_pack",
        refresh_command="python -m lynchpin.cli.current_state --refresh-substrate --start 2013-01-01 --end $(date +%F)",
        query_mode="substrate",
    ),
    SourceContract(
        name="health",
        authority="Samsung Health raw exports",
        query_surface="lynchpin.sources.health",
        refresh_command="python -m lynchpin.cli.process_health",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="sleep",
        authority="Samsung Health/Sleep-as-Android exports",
        query_surface="lynchpin.sources.sleep",
        refresh_command="python -m lynchpin.cli.process_health",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="substance",
        authority="processed substance log CSV",
        query_surface="lynchpin.sources.substance",
        refresh_command="edit /realm/data/exports/health/processed/substance_log_unified.csv",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="spotify",
        authority="Spotify GDPR export directories",
        query_surface="lynchpin.sources.spotify",
        refresh_command="python -m lynchpin.ingest.exports_materialize spotify",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="reddit",
        authority="Reddit GDPR export directories",
        query_surface="lynchpin.sources.reddit",
        refresh_command="python -m lynchpin.ingest.exports_materialize reddit",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="facebook_messenger",
        authority="Facebook Messenger GDPR export",
        query_surface="lynchpin.sources.exports",
        refresh_command="python -m lynchpin.ingest.exports_materialize facebook-messenger",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="communications",
        authority="canonical Messenger plus parseable Outlook communication exports",
        query_surface="lynchpin.sources.communications",
        refresh_command="python -m lynchpin.ingest.communications_materialize",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="raindrop",
        authority="Raindrop export CSVs",
        query_surface="lynchpin.sources.exports",
        refresh_command="python -m lynchpin.ingest.exports_materialize raindrop",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="browser_bookmarks",
        authority="browser bookmark exports and Firefox/Vivaldi profile data",
        query_surface="lynchpin.sources.bookmarks",
        refresh_command="python -m lynchpin.ingest.bookmarks_materialize",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="arbtt",
        authority="ARBTT capture.log files",
        query_surface="lynchpin.sources.arbtt",
        refresh_command="python -m lynchpin.ingest.arbtt_materialize",
        substrate_daily_signal=True,
    ),
    SourceContract(
        name="machine",
        authority="machine telemetry SQLite/JSONL captures",
        query_surface="lynchpin.sources.machine plus analysis machine artifacts",
        refresh_command="python -m lynchpin.ingest.machine_materialize",
    ),
    SourceContract(
        name="spotify_daily",
        authority="canonical Spotify stream materialization",
        query_surface="lynchpin.sources.personal_signals.iter_spotify_daily_signals",
        refresh_command="python -m lynchpin.ingest.personal_signals_materialize spotify-daily",
        query_mode="canonical",
    ),
    SourceContract(
        name="personal_daily_signals",
        authority="canonical personal-source products",
        query_surface="lynchpin.sources.personal_signals.iter_personal_daily_signals",
        refresh_command="python -m lynchpin.ingest.personal_signals_materialize personal-daily-signals",
        query_mode="canonical",
    ),
    SourceContract(
        name="irc",
        authority="raw WeeChat IRC logs under irc_root/_raw",
        query_surface="lynchpin.sources.irc_raw",
        refresh_command="python -m lynchpin.ingest.irc_materialize",
        required=False,
    ),
)

_CONTRACT_CAPABILITIES: dict[str, dict[str, Any]] = {
    "webhistory": {
        "collection_model": "continuous",
        "graph_node_kinds": ("web_domain_day",),
        "mcp_tools": ("web_daily", "webhistory_provenance", "personal_daily_signals"),
        "caveats": (
            "graph layer emits daily domain aggregates rather than per-visit nodes",
            "weak_* web buckets are host/path matches, not semantic classification",
        ),
    },
    "google_takeout": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("google_activity_day",),
        "mcp_tools": ("google_takeout_daily", "google_takeout_events", "personal_daily_signals"),
        "caveats": (
            "typed activity/event surface excludes contacts and asset inventories",
            "YouTube subscription/export rows without timestamps are inventory, not activity",
        ),
    },
    "polylogue": {
        "collection_model": "continuous",
        "graph_node_kinds": ("ai_session", "ai_work_event"),
        "mcp_tools": ("(polylogue MCP server)",),
        "caveats": ("deep Polylogue analytics live on the Polylogue MCP server",),
    },
    "activitywatch": {
        "collection_model": "continuous",
        "substrate_tables": ("activity_content_day", "activity_content_bucket", "activity_title_usage"),
        "graph_node_kinds": (
            "focus_span", "focus_day", "deep_work_block", "focus_loop",
            "attention_day", "circadian_profile", "fragmentation_day", "activity_content_day",
        ),
        "mcp_tools": (
            "focus_daily", "activity_content_daily", "activity_content_coverage",
            "activity_title_usage", "activity_unmatched_titles",
        ),
    },
    "title_metadata": {
        "collection_model": "metadata",
        "substrate_tables": ("title_classification",),
        "mcp_tools": ("title_metadata_status", "title_metadata_audit", "activity_title_usage", "activity_unmatched_titles"),
        "caveats": ("historical GPT/rules classifications; inspect coverage before semantic use",),
    },
    "activity_content": {
        "collection_model": "derived",
        "substrate_tables": ("activity_content_day", "activity_content_bucket", "activity_title_usage"),
        "graph_node_kinds": ("activity_content_day",),
        "mcp_tools": ("activity_content_daily", "activity_content_coverage", "activity_title_usage", "activity_unmatched_titles"),
        "caveats": ("coverage is bounded by title metadata matches; unmatched-title queue is the audit surface",),
    },
    "atuin": {
        "collection_model": "continuous",
        "graph_node_kinds": ("terminal_session", "terminal_pattern"),
        "mcp_tools": ("terminal_daily", "terminal_sessions"),
    },
    "evidence_graph_substrate": {
        "collection_model": "stage",
        "substrate_tables": (
            "commit_fact", "file_change_fact", "symbol_change", "ai_work_event",
            "pr_review_row", "evidence_graph_build", "evidence_node",
            "evidence_edge", "analysis_claim", "substrate_promotion_run",
            "substrate_source_status",
        ),
        "graph_node_kinds": ("commit", "ai_work_event", "ai_session", "analysis_claim"),
        "mcp_tools": (
            "query_substrate", "list_substrate_tables", "substrate_readiness_report",
            "substrate_source_status", "load_evidence_graph_summary",
            "list_evidence_graph_builds", "project_day_correlations",
            "closure_chain_walks", "file_overlap_edges", "symbol_overlap_edges",
            "analysis_claims", "claim_evidence", "promotion_runs",
        ),
    },
    "health": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("health_metric",),
        "mcp_tools": ("personal_daily_signals", "health_trend"),
        "caveats": ("export-backed; absence of recent rows may mean no export, not necessarily zero activity",),
    },
    "sleep": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("sleep_quality", "readiness_forecast"),
        "mcp_tools": ("personal_daily_signals",),
    },
    "substance": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "mcp_tools": ("personal_daily_signals",),
        "caveats": ("manual processed CSV; coverage shows recorded rows only",),
    },
    "spotify": {
        "collection_model": "event_export",
        "substrate_tables": ("spotify_daily", "personal_daily_signal"),
        "graph_node_kinds": ("listening_session",),
        "mcp_tools": ("spotify_daily", "personal_daily_signals"),
    },
    "reddit": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "mcp_tools": ("personal_daily_signals",),
        "caveats": ("export-backed; no rows in a window can mean no use or no export coverage",),
    },
    "facebook_messenger": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("communication_activity",),
        "mcp_tools": ("communication_events", "communication_daily"),
        "caveats": ("superseded for unified access by communications",),
    },
    "communications": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("communication_activity",),
        "mcp_tools": ("communication_events", "communication_daily"),
        "caveats": ("Teams candidate files are not promoted unless real message/call exports are found",),
    },
    "raindrop": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("bookmark_activity",),
        "mcp_tools": ("bookmarks_search", "bookmark_daily"),
        "caveats": ("dedup with browser bookmarks is coarse",),
    },
    "browser_bookmarks": {
        "collection_model": "event_export",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("bookmark_activity",),
        "mcp_tools": ("bookmarks_search", "bookmark_daily"),
    },
    "arbtt": {
        "collection_model": "historical",
        "substrate_tables": ("personal_daily_signal",),
        "graph_node_kinds": ("arbtt_focus_activity",),
        "mcp_tools": ("arbtt_focus_daily",),
        "caveats": ("historical focus source; weaker title/category attribution than ActivityWatch",),
    },
    "machine": {
        "collection_model": "continuous",
        "substrate_tables": (
            "machine_metric_sample", "machine_gpu_sample",
            "machine_service_state", "machine_network_sample",
            "machine_experiment_run",
        ),
        "mcp_tools": (
            "machine_metrics_daily", "machine_episodes", "machine_context_windows",
            "machine_below_attributions", "machine_observational_baselines",
            "machine_experiment_claims", "machine_service_state_summary",
            "machine_gap_summary", "machine_bufferbloat_summary",
            "borg_drill_history", "sinnix_generation_history",
        ),
    },
    "spotify_daily": {
        "collection_model": "derived",
        "substrate_tables": ("spotify_daily",),
        "mcp_tools": ("spotify_daily", "derived_product_status"),
    },
    "personal_daily_signals": {
        "collection_model": "derived",
        "substrate_tables": ("personal_daily_signal", "activity_content_day", "activity_content_bucket", "activity_title_usage"),
        "graph_node_kinds": ("health_metric", "sleep_quality", "communication_activity", "bookmark_activity", "activity_content_day"),
        "mcp_tools": ("personal_daily_signals", "derived_product_status"),
    },
    "irc": {
        "collection_model": "continuous",
        "graph_node_kinds": ("communication_activity",),
        "caveats": (
            "WeeChat raw log parsing; meta/server lines flagged separately",
            "nick normalization is heuristic; see _KNOWN_ALIASES for explicit mappings",
        ),
    },
}

SOURCE_CONTRACTS = tuple(
    replace(contract, **_CONTRACT_CAPABILITIES.get(contract.name, {}))
    for contract in SOURCE_CONTRACTS
)

PROMOTION_STAGE_CONTRACTS: tuple[StageContract, ...] = (
    StageContract("commits", "commit_fact"),
    StageContract("file_changes", "file_change_fact"),
    StageContract("symbols", "symbol_change", required=False),
    StageContract("ai_work_events", "ai_work_event"),
    StageContract("evidence_graph", "evidence_graph_build"),
    StageContract("pr_review", "pr_review_row", required=False),
    StageContract("spotify_daily", "spotify_daily", required=False),
    StageContract("personal_daily_signal", "personal_daily_signal"),
    StageContract("title_classification", "title_classification", required=False),
    StageContract("activity_content", "activity_content_day", required=False),
    StageContract("machine", "machine_metric_sample", required=False),
    StageContract("machine_gpu_sample", "machine_gpu_sample", required=False),
    StageContract("machine_network_sample", "machine_network_sample", required=False),
    StageContract("machine_service_state", "machine_service_state", required=False),
    StageContract("machine_experiments", "machine_experiment_run", required=False),
    StageContract("sinnix_generation", "sinnix_generation", required=False),
    StageContract("borg_drill_run", "borg_drill_run", required=False),
)

SOURCE_CONTRACT_BY_NAME = {contract.name: contract for contract in SOURCE_CONTRACTS}
SOURCE_CONTRACT_NAMES = tuple(contract.name for contract in SOURCE_CONTRACTS)
PROMOTION_STAGE_CONTRACT_BY_NAME = {
    contract.name: contract for contract in PROMOTION_STAGE_CONTRACTS
}
PROMOTION_STAGE_NAMES = tuple(contract.name for contract in PROMOTION_STAGE_CONTRACTS)
DAILY_SIGNAL_SOURCE_NAMES = tuple(
    contract.name for contract in SOURCE_CONTRACTS if contract.substrate_daily_signal
)


def source_contract(name: str) -> SourceContract:
    return SOURCE_CONTRACT_BY_NAME[name]


def stage_contract(name: str) -> StageContract:
    return PROMOTION_STAGE_CONTRACT_BY_NAME[name]


def dataset_status_to_substrate_status(status: DatasetStatus | str) -> SubstrateStatus:
    """Map dataset audit statuses to substrate readiness statuses."""
    if status == "ready":
        return "ok"
    if status == "empty":
        return "empty"
    if status in {"missing", "partial"}:
        return "unavailable"
    return "error"


def source_empty_substrate_status(empty: SourceEmptiness) -> SubstrateStatus:
    """Return the readiness status for a source that ran and produced zero rows."""
    if empty == "valid":
        return "empty"
    if empty == "degraded":
        return "unavailable"
    return "error"


__all__ = [
    "DAILY_SIGNAL_SOURCE_NAMES",
    "CollectionModel",
    "DatasetStatus",
    "PROMOTION_STAGE_CONTRACTS",
    "PROMOTION_STAGE_CONTRACT_BY_NAME",
    "PROMOTION_STAGE_NAMES",
    "SOURCE_CONTRACTS",
    "SOURCE_CONTRACT_BY_NAME",
    "SOURCE_CONTRACT_NAMES",
    "SourceContract",
    "StageContract",
    "SubstrateStatus",
    "dataset_status_to_substrate_status",
    "source_empty_substrate_status",
    "source_contract",
    "stage_contract",
]
