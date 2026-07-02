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
    parameters: tuple[str, ...] = ()
    views: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    response_kind: str = "object"
    examples: tuple[dict[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "effect_mode": self.effect_mode,
            "parameters": list(self.parameters),
            "views": list(self.views),
            "requires": list(self.requires),
            "response_kind": self.response_kind,
            "examples": [dict(example) for example in self.examples],
        }


@dataclass(frozen=True)
class PublicToolSpec:
    name: str
    group: str
    description: str
    effect_mode: EffectMode
    actions: tuple[ActionSpec, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "description": self.description,
            "effect_mode": self.effect_mode,
            "actions": [action.to_json() for action in self.actions],
        }


PUBLIC_TOOLS: tuple[PublicToolSpec, ...] = (
    PublicToolSpec(
        name="lynchpin_status",
        group="orientation",
        description="Runtime, readiness, materialization, chisel, and GitHub status.",
        effect_mode="read",
        actions=(
            ActionSpec("runtime", "Code path, git revision, MCP count, and substrate state.", response_kind="status"),
            ActionSpec("snapshot", "Compact current-state orientation snapshot for small-context agents.", parameters=("start", "end"), response_kind="situation_snapshot"),
            ActionSpec("readiness", "Substrate and source readiness summary.", parameters=("start", "end"), response_kind="readiness"),
            ActionSpec("self_check", "Public registry and metadata consistency check.", response_kind="self_check"),
            ActionSpec("materialization", "Canonical product materialization audit.", response_kind="materialization_audit"),
            ActionSpec("operations", "Recent operation receipts and supported actions.", response_kind="operation_receipts"),
            ActionSpec("chisel", "Code snapshot/chisel materialization status.", response_kind="snapshot_status"),
            ActionSpec("github", "GitHub context materialization status.", response_kind="materialization_status"),
        ),
    ),
    PublicToolSpec(
        name="lynchpin_catalog",
        group="orientation",
        description="Public tool/action catalog, source contracts, and query schemas.",
        effect_mode="read",
        actions=(
            ActionSpec(
                "catalog",
                "Public tool/action catalog, source contracts, query entities, and examples.",
                parameters=("domain", "include_schema"),
                response_kind="catalog",
            ),
        ),
    ),
    PublicToolSpec(
        name="lynchpin_query",
        group="query",
        description="Read-only JSON query DSL and SELECT-only SQL bridge over the substrate.",
        effect_mode="read",
        actions=(
            ActionSpec(
                "dsl",
                "Structured table/entity query with filters, ordering, and limits.",
                parameters=("entity", "table", "select", "where", "time", "order_by", "limit", "explain"),
                response_kind="query_result",
                examples=({"entity": "commits", "select": ["sha", "repo"], "where": {"repo": "lynchpin"}, "limit": 20},),
            ),
            ActionSpec(
                "sql",
                "SELECT-only SQL query with parameters and row cap.",
                parameters=("sql", "parameters", "max_rows"),
                response_kind="query_result",
                examples=({"mode": "sql", "sql": "SELECT COUNT(*) AS cnt FROM commit_fact"},),
            ),
        ),
    ),
    PublicToolSpec(
        name="lynchpin_evidence",
        group="evidence",
        description="Evidence graph, claims, walks, coverage, confidence, and cross-reference views.",
        effect_mode="converge",
        actions=(
            ActionSpec("graph", "Evidence graph build list or summary.", "converge", parameters=("refresh_id", "start", "end"), response_kind="evidence_graph"),
            ActionSpec("timeline", "Evidence/project-day timeline rows.", "converge", parameters=("refresh_id", "start", "end", "project", "limit"), response_kind="timeline"),
            ActionSpec("walk", "Walk evidence graph edges from a node.", "converge", parameters=("start_id", "refresh_id", "limit"), requires=("start_id",), response_kind="evidence_walk"),
            ActionSpec("claims", "Analysis claims.", "converge", parameters=("start", "end", "project", "refresh_id", "limit"), response_kind="analysis_claims"),
            ActionSpec("claim_evidence", "Evidence for one analysis claim.", "converge", parameters=("claim_id", "refresh_id", "limit"), requires=("claim_id",), response_kind="claim_evidence"),
            ActionSpec("coverage", "Source contract coverage.", "read", parameters=("project", "start", "end"), response_kind="coverage"),
            ActionSpec("confidence", "Substrate confidence matrix.", "read", parameters=("refresh_id",), response_kind="confidence_matrix"),
            ActionSpec("crossref", "Cross-source URL/reference aggregation.", "converge", parameters=("start", "end", "limit"), requires=("start", "end"), response_kind="crossref"),
        ),
    ),
    PublicToolSpec(
        name="lynchpin_project",
        group="project",
        description="Repository, code velocity, hotspots, GitHub, reviews, and code snapshots.",
        effect_mode="converge",
        actions=(
            ActionSpec("repos", "Known repo names and roots.", response_kind="repo_list"),
            ActionSpec("files", "Repo file listing.", parameters=("repo", "project", "limit"), requires=("repo_or_project",), response_kind="repo_files"),
            ActionSpec("commits", "Recent repo commits.", parameters=("repo", "project", "limit"), requires=("repo_or_project",), response_kind="commits"),
            ActionSpec("velocity", "Code velocity and throughput views.", "converge", parameters=("repo", "project", "view", "start", "end"), views=("throughput", "daily", "weekly"), response_kind="velocity"),
            ActionSpec("hotspots", "File/symbol hotspots and refactor candidates.", "converge", parameters=("repo", "project", "view", "limit"), views=("files", "symbols", "refactors"), response_kind="hotspots"),
            ActionSpec("change_kinds", "Commit conventional/breaking/AI attribution views.", "converge", parameters=("repo", "project", "view"), views=("conventional", "breaking", "ai"), response_kind="change_analysis"),
            ActionSpec("github", "GitHub issue/PR list and detail.", "read", parameters=("repo", "project", "view", "number", "state"), views=("prs", "issues", "issue"), response_kind="github_items"),
            ActionSpec("reviews", "PR review rows and bottlenecks.", "converge", parameters=("repo", "project", "view"), response_kind="reviews"),
            ActionSpec("snapshots", "Chisel/code snapshot status, slices, and audit.", "read", parameters=("repo", "project", "view"), views=("status", "runs", "slices", "audit"), response_kind="code_snapshots"),
        ),
    ),
    PublicToolSpec(
        name="lynchpin_personal",
        group="personal",
        description="Operator, personal signals, health, communications, web, bookmarks, and reports.",
        effect_mode="converge",
        actions=(
            ActionSpec("daily", "Normalized personal daily signals.", "converge", parameters=("start", "end", "source", "limit"), response_kind="personal_daily"),
            ActionSpec("activity", "ActivityWatch/activity-content/focus views.", "converge", parameters=("view", "start", "end", "limit"), views=("daily", "focus", "buckets", "titles"), response_kind="activity"),
            ActionSpec("health", "Health trend and raw wearable detail views.", "read", parameters=("view", "start", "end"), views=("trend", "daily", "stress", "heart_rate", "hrv"), response_kind="health"),
            ActionSpec("communications", "Communication events and daily summaries.", "converge", parameters=("view", "start", "end", "limit"), response_kind="communications"),
            ActionSpec("web", "Web and Google Takeout activity.", "converge", parameters=("view", "start", "end", "query", "limit"), views=("daily", "domains", "takeout"), response_kind="web_activity"),
            ActionSpec("bookmarks", "Bookmark search and daily summaries.", "converge", parameters=("view", "query", "start", "end", "limit"), response_kind="bookmarks"),
            ActionSpec("media", "Spotify/media daily summaries.", "read", parameters=("start", "end"), response_kind="media_daily"),
            ActionSpec("operator", "Operator rhythm/readiness/retrospective views.", "converge", parameters=("view", "start", "end", "project"), views=("rhythm", "readiness", "verify_vs_edit_ratio"), response_kind="operator"),
            ActionSpec("reports", "Generated cross-source personal analysis reports.", "read", parameters=("view", "project"), views=("anomaly", "life_phase", "productivity", "substance", "burnout", "ai_efficiency"), response_kind="analysis_report"),
        ),
    ),
    PublicToolSpec(
        name="lynchpin_machine",
        group="machine",
        description="Machine telemetry, pressure, services, workloads, observations, benchmarks, and diagnostics.",
        effect_mode="converge",
        actions=(
            ActionSpec("status", "Machine status and materialization health.", "read", parameters=("view",), views=("summary", "materialization"), response_kind="machine_status"),
            ActionSpec("metrics", "Daily/context/memory/pressure machine metrics.", "converge", parameters=("view", "start", "end", "host"), views=("daily", "context", "memory", "pressure"), response_kind="machine_metrics"),
            ActionSpec("pressure", "Pressure reports and explainers.", "converge", parameters=("view", "start", "end", "host", "limit"), views=("report", "explain"), response_kind="pressure"),
            ActionSpec("services", "Service state, generations, and backup drills.", "converge", parameters=("view", "start", "end", "host", "limit"), response_kind="services"),
            ActionSpec("workloads", "Machine workload/session/timeline views.", "read", parameters=("view", "start", "end"), views=("summary", "sessions", "co_presence", "scope", "heatmap", "orphans"), response_kind="workloads"),
            ActionSpec("observations", "Work observation and command performance views.", "converge", parameters=("view", "start", "end", "project", "limit"), response_kind="work_observations"),
            ActionSpec("benchmarks", "Benchmark, validation, and matched-experiment views.", "read", parameters=("view", "limit"), response_kind="benchmarks"),
            ActionSpec("diagnostics", "Attribution, assumptions, support, and dataset diagnostics.", "read", parameters=("view", "project", "limit"), response_kind="diagnostics"),
            ActionSpec("windows", "Machine context/work-state windows.", "converge", parameters=("view", "start", "end", "project", "limit"), response_kind="windows"),
        ),
    ),
    PublicToolSpec(
        name="lynchpin_ops",
        group="operations",
        description="Auditable local write/convergence operations; dry-run by default.",
        effect_mode="write",
        actions=(
            ActionSpec("materialize", "Inspect or force transparent local/derived product materialization.", "write", parameters=("execute", "source", "start", "end", "force"), response_kind="operation"),
            ActionSpec("github_refresh", "Refresh GitHub context products.", "write", parameters=("execute", "source"), response_kind="operation"),
            ActionSpec("chisel", "Run chisel/code-snapshot generation.", "write", parameters=("execute", "source"), response_kind="operation"),
            ActionSpec("ai_backfill", "Backfill AI attribution in substrate.", "write", parameters=("execute", "refresh_id"), response_kind="operation"),
            ActionSpec("promote_artifact", "Promote an analysis product into evidence.", "write", parameters=("execute", "title", "path", "refresh_id"), requires=("title", "path"), response_kind="operation"),
            ActionSpec("prune", "Prune old substrate graph builds.", "write", parameters=("execute", "limit"), response_kind="operation"),
            ActionSpec("receipt", "Read operation receipts.", "read", parameters=("source", "limit"), response_kind="operation_receipts"),
        ),
    ),
)

PUBLIC_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in PUBLIC_TOOLS)
PUBLIC_TOOL_BY_NAME: dict[str, PublicToolSpec] = {tool.name: tool for tool in PUBLIC_TOOLS}


def public_tool_catalog() -> list[dict[str, Any]]:
    return [tool.to_json() for tool in PUBLIC_TOOLS]


def public_action_names(tool_name: str) -> tuple[str, ...]:
    spec = PUBLIC_TOOL_BY_NAME[tool_name]
    return tuple(action.name for action in spec.actions)


def public_action_spec(tool_name: str, action_name: str) -> ActionSpec | None:
    spec = PUBLIC_TOOL_BY_NAME[tool_name]
    for action in spec.actions:
        if action.name == action_name:
            return action
    return None
