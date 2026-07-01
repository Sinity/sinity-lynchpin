"""Public MCP registry metadata for the collapsed Lynchpin surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EffectMode = Literal["read", "converge", "write"]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    effect_mode: EffectMode = "read"

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "effect_mode": self.effect_mode,
        }


@dataclass(frozen=True)
class PublicToolSpec:
    name: str
    group: str
    description: str
    effect_mode: EffectMode
    actions: tuple[ActionSpec, ...] = ()
    legacy_tools: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "description": self.description,
            "effect_mode": self.effect_mode,
            "actions": [action.to_json() for action in self.actions],
            "legacy_tools": list(self.legacy_tools),
        }


PUBLIC_TOOLS: tuple[PublicToolSpec, ...] = (
    PublicToolSpec(
        name="lynchpin_status",
        group="orientation",
        description="Runtime, readiness, materialization, chisel, and GitHub status.",
        effect_mode="read",
        actions=(
            ActionSpec("runtime", "Code path, git revision, MCP count, and substrate state."),
            ActionSpec("readiness", "Substrate and source readiness summary."),
            ActionSpec("self_check", "Public registry and metadata consistency check."),
            ActionSpec("materialization", "Canonical product materialization audit."),
            ActionSpec("operations", "Recent operation receipts and supported actions."),
            ActionSpec("chisel", "Code snapshot/chisel materialization status."),
            ActionSpec("github", "GitHub context materialization status."),
        ),
        legacy_tools=("mcp_status", "observability_status", "materialization_status"),
    ),
    PublicToolSpec(
        name="lynchpin_catalog",
        group="orientation",
        description="Public tool/action catalog, source contracts, schemas, and legacy route map.",
        effect_mode="read",
        legacy_tools=("mcp_guide", "mcp_capability_matrix", "list_substrate_tables"),
    ),
    PublicToolSpec(
        name="lynchpin_query",
        group="query",
        description="Read-only JSON query DSL and SELECT-only SQL bridge over the substrate.",
        effect_mode="read",
        actions=(
            ActionSpec("dsl", "Structured table/entity query with filters, ordering, and limits."),
            ActionSpec("sql", "SELECT-only SQL query with parameters and row cap."),
        ),
        legacy_tools=("query_substrate",),
    ),
    PublicToolSpec(
        name="lynchpin_evidence",
        group="evidence",
        description="Evidence graph, claims, walks, coverage, confidence, and cross-reference views.",
        effect_mode="converge",
        actions=(
            ActionSpec("graph", "Evidence graph build list or summary.", "converge"),
            ActionSpec("timeline", "Evidence/project-day timeline rows.", "converge"),
            ActionSpec("walk", "Walk evidence graph edges from a node.", "converge"),
            ActionSpec("claims", "Analysis claims.", "converge"),
            ActionSpec("claim_evidence", "Evidence for one analysis claim.", "converge"),
            ActionSpec("coverage", "Source contract coverage.", "read"),
            ActionSpec("confidence", "Substrate confidence matrix.", "read"),
            ActionSpec("crossref", "Cross-source URL/reference aggregation.", "converge"),
        ),
        legacy_tools=(
            "evidence_graph",
            "analysis_evidence",
            "project_day_correlations",
            "walk_evidence",
            "overlap_edges",
            "closure_chain_walks",
            "contract_coverage",
            "substrate_confidence_matrix",
            "url_crossref",
        ),
    ),
    PublicToolSpec(
        name="lynchpin_project",
        group="project",
        description="Repository, code velocity, hotspots, GitHub, reviews, and code snapshots.",
        effect_mode="converge",
        actions=(
            ActionSpec("repos", "Known repo names and roots."),
            ActionSpec("files", "Repo file listing."),
            ActionSpec("commits", "Recent repo commits."),
            ActionSpec("velocity", "Code velocity and throughput views.", "converge"),
            ActionSpec("hotspots", "File/symbol hotspots and refactor candidates.", "converge"),
            ActionSpec("change_kinds", "Commit conventional/breaking/AI attribution views.", "converge"),
            ActionSpec("github", "GitHub issue/PR list and detail.", "read"),
            ActionSpec("reviews", "PR review rows and bottlenecks.", "converge"),
            ActionSpec("snapshots", "Chisel/code snapshot status and slices.", "read"),
        ),
        legacy_tools=(
            "repo_names",
            "repo_file_list",
            "repo_recent_commits",
            "velocity",
            "code_velocity",
            "code_hotspots",
            "commit_analysis",
            "review",
            "list_github_issues",
            "get_github_issue",
            "list_github_prs",
            "get_github_pr",
            "code_snapshots",
        ),
    ),
    PublicToolSpec(
        name="lynchpin_personal",
        group="personal",
        description="Operator, personal signals, health, communications, web, bookmarks, and reports.",
        effect_mode="converge",
        actions=(
            ActionSpec("daily", "Normalized personal daily signals.", "converge"),
            ActionSpec("activity", "ActivityWatch/activity-content/focus views.", "converge"),
            ActionSpec("health", "Health trend and raw wearable detail views.", "read"),
            ActionSpec("communications", "Communication events and daily summaries.", "converge"),
            ActionSpec("web", "Web and Google Takeout activity.", "converge"),
            ActionSpec("bookmarks", "Bookmark search and daily summaries.", "converge"),
            ActionSpec("media", "Spotify/media daily summaries.", "read"),
            ActionSpec("operator", "Operator rhythm/readiness/retrospective views.", "converge"),
            ActionSpec("reports", "Generated cross-source personal analysis reports.", "read"),
        ),
        legacy_tools=(
            "personal_daily_signals",
            "operator",
            "rhythm",
            "activity_content",
            "focus_daily",
            "health_trend",
            "health_daily_summary",
            "communication",
            "web",
            "google_takeout",
            "bookmarks",
            "spotify_daily",
            "anomaly_crossref_report",
            "life_phase_report",
            "productivity_predictors_report",
            "substance_health_report",
            "burnout_warning_report",
            "ai_session_efficiency_report",
        ),
    ),
    PublicToolSpec(
        name="lynchpin_machine",
        group="machine",
        description="Machine telemetry, pressure, services, workloads, observations, benchmarks, and diagnostics.",
        effect_mode="converge",
        actions=(
            ActionSpec("status", "Machine status and materialization health.", "read"),
            ActionSpec("metrics", "Daily/context/memory/pressure machine metrics.", "converge"),
            ActionSpec("pressure", "Pressure reports and explainers.", "converge"),
            ActionSpec("services", "Service state, generations, and backup drills.", "converge"),
            ActionSpec("workloads", "Machine workload/session/timeline views.", "read"),
            ActionSpec("observations", "Work observation and command performance views.", "converge"),
            ActionSpec("benchmarks", "Benchmark, validation, and matched-experiment views.", "read"),
            ActionSpec("diagnostics", "Attribution, assumptions, support, and dataset diagnostics.", "read"),
            ActionSpec("windows", "Machine context/work-state windows.", "converge"),
        ),
        legacy_tools=(
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
    ),
    PublicToolSpec(
        name="lynchpin_ops",
        group="operations",
        description="Auditable local write/convergence operations; dry-run by default.",
        effect_mode="write",
        actions=(
            ActionSpec("materialize", "Plan or run local materialization.", "write"),
            ActionSpec("github_refresh", "Refresh GitHub context products.", "write"),
            ActionSpec("chisel", "Run chisel/code-snapshot generation.", "write"),
            ActionSpec("ai_backfill", "Backfill AI attribution in substrate.", "write"),
            ActionSpec("promote_artifact", "Promote an analysis product into evidence.", "write"),
            ActionSpec("prune", "Prune old substrate graph builds.", "write"),
            ActionSpec("receipt", "Read operation receipts.", "read"),
        ),
        legacy_tools=("ai_attribution_backfill", "substrate_prune", "promote_analysis_product"),
    ),
)

PUBLIC_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in PUBLIC_TOOLS)
PUBLIC_TOOL_BY_NAME: dict[str, PublicToolSpec] = {tool.name: tool for tool in PUBLIC_TOOLS}
LEGACY_TOOL_MAP: dict[str, dict[str, str]] = {}

for tool in PUBLIC_TOOLS:
    for legacy_name in tool.legacy_tools:
        LEGACY_TOOL_MAP[legacy_name] = {"tool": tool.name, "group": tool.group}


def public_tool_catalog() -> list[dict[str, Any]]:
    return [tool.to_json() for tool in PUBLIC_TOOLS]


def public_action_names(tool_name: str) -> tuple[str, ...]:
    spec = PUBLIC_TOOL_BY_NAME[tool_name]
    return tuple(action.name for action in spec.actions)

