"""Current-state evidence inventory helpers.

This module builds compact, read-only facts for broad state analysis. It avoids
doing the synthesis itself; the output is meant to feed reports, prompts, and
later cross-source correlation.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from ..analysis.core.git import run_git
from ..core.evidence import CostClass, SourceReadinessReport
from ..core.evidence_graph import EvidenceGraph, EvidenceNode
from ..core.projects import ALL_PROJECTS
from ..core.projects import canonical_project_name
from ..core.projects import project_path
from ..sources.github import GitHubItem, GitHubLifecycleClassification, classify_lifecycle, fetch_issues, fetch_prs, repo_slug
from .evidence_graph import build_evidence_graph
from .evidence_views import render_evidence_graph_summary
from .movement import MovementSummary, movement_summary, render_movement_summary
from .source_readiness import render_source_readiness, source_readiness
from .work_correlation import (
    CorrelatedWorkDay,
    WorkCorrelationSummary,
    render_work_correlation_summary,
    render_work_day_correlations,
    strongest_work_correlations,
    summarize_work_correlations,
    work_day_correlations,
)

if TYPE_CHECKING:
    from ..sources.polylogue import PolylogueReadiness


@dataclass(frozen=True)
class ProjectInventoryItem:
    name: str
    path: Path
    exists: bool
    is_git_repo: bool
    branch: str | None
    default_branch: str | None
    head: str | None
    dirty: bool
    ahead: int | None
    behind: int | None
    last_commit_at: datetime | None
    last_commit_subject: str | None
    github_slug: str | None
    active_registry_entry: bool


@dataclass(frozen=True)
class ProjectGitHubFrontierItem:
    project: str
    kind: str
    number: int
    title: str
    state: str
    url: str | None
    lifecycle: GitHubLifecycleClassification
    label_names: tuple[str, ...]
    comment_count: int
    review_decision: str | None = None
    review_count: int = 0
    review_comment_count: int = 0


@dataclass(frozen=True)
class ProjectGitHubFrontier:
    project: str
    slug: str | None
    status: str
    reason: str | None
    items: tuple[ProjectGitHubFrontierItem, ...]


@dataclass(frozen=True)
class CurrentStateEvidencePack:
    start: datetime
    end: datetime
    generated_at: datetime
    inventory: tuple[ProjectInventoryItem, ...]
    polylogue_readiness: PolylogueReadiness
    evidence_graph: EvidenceGraph
    source_readiness: SourceReadinessReport
    work_correlations: tuple[CorrelatedWorkDay, ...]
    correlation_summary: WorkCorrelationSummary
    movement: MovementSummary
    github_frontiers: tuple[ProjectGitHubFrontier, ...] = ()


def archive_readiness(*args: Any, **kwargs: Any) -> "PolylogueReadiness":
    from ..sources.polylogue import archive_readiness as impl

    return impl(*args, **kwargs)


def project_inventory(
    *,
    roots: Sequence[Path] | None = None,
    include_unregistered: bool = True,
) -> tuple[ProjectInventoryItem, ...]:
    """Return local project checkout state for registry and discovered repos."""
    candidates: dict[Path, tuple[str, bool]] = {}
    for name, entry in ALL_PROJECTS.items():
        candidates[project_path(name)] = (name, entry.active)

    if include_unregistered:
        for root in roots or (Path("/realm/project"),):
            if not root.exists():
                continue
            for child in root.iterdir():
                if child.name.startswith(".") or child.name == "_inactive":
                    continue
                if (child / ".git").exists() and child.resolve() not in candidates:
                    candidates[child.resolve()] = (child.name, True)

    return tuple(_inventory_one(name, path, active) for path, (name, active) in sorted(candidates.items(), key=lambda x: x[1][0]))


def active_project_inventory(*, max_age_days: int = 45) -> tuple[ProjectInventoryItem, ...]:
    """Return projects likely relevant to current-state analysis."""
    now = datetime.now(timezone.utc)
    items = []
    for item in project_inventory():
        if not item.exists or not item.is_git_repo:
            continue
        if not item.active_registry_entry and _is_inactive_path(item.path):
            continue
        if item.active_registry_entry or item.dirty:
            items.append(item)
            continue
        if item.last_commit_at is not None:
            age_days = (now - item.last_commit_at.astimezone(timezone.utc)).days
            if age_days <= max_age_days:
                items.append(item)
    return tuple(items)


def project_github_frontier(
    inventory: Sequence[ProjectInventoryItem],
    *,
    open_limit: int = 100,
    closed_limit: int = 40,
    closed_pr_limit: int = 40,
) -> tuple[ProjectGitHubFrontier, ...]:
    """Fetch open/recently closed GitHub work frontier for inventory items."""
    frontiers: list[ProjectGitHubFrontier] = []
    for item in inventory:
        if not item.exists or not item.is_git_repo or item.github_slug is None:
            continue
        collected: list[GitHubItem] = []
        statuses: list[str] = []
        reasons: list[str] = []
        for result in (
            fetch_issues(item.path, state="open", limit=open_limit),
            fetch_issues(item.path, state="closed", limit=closed_limit),
            fetch_prs(item.path, state="open", limit=open_limit),
            fetch_prs(item.path, state="closed", limit=closed_pr_limit),
        ):
            statuses.append(result.status)
            if result.reason:
                reasons.append(result.reason)
            collected.extend(result.items)
        status = "ok" if statuses and all(status == "ok" for status in statuses) else "degraded"
        frontiers.append(
            ProjectGitHubFrontier(
                project=item.name,
                slug=item.github_slug,
                status=status,
                reason="; ".join(reasons) if reasons else None,
                items=tuple(_frontier_item(item.name, gh_item) for gh_item in collected),
            )
        )
    return tuple(frontiers)


def current_state_evidence_pack(
    *,
    start: datetime,
    end: datetime,
    projects: Sequence[str] | None = None,
    include_github_frontier: bool = False,
    graph: EvidenceGraph | None = None,
    mode: CostClass | None = None,
) -> CurrentStateEvidencePack:
    """Build a compact evidence pack for current-state analysis."""
    start_date = start.date()
    end_date = end.date()
    effective_mode: CostClass = mode or (graph.mode if graph is not None else "network" if include_github_frontier else "local-fast")
    include_github = include_github_frontier or effective_mode == "network"
    selected = _selected_projects(projects)
    inventory_source = _selected_project_inventory(selected) if selected else active_project_inventory()
    inventory = _filter_inventory(inventory_source, selected=selected)
    evidence_graph = graph or build_evidence_graph(
        start=start_date,
        end=end_date,
        projects=projects,
        mode=effective_mode,
    )
    correlations = work_day_correlations(
        start=start_date,
        end=end_date,
        include_github_context=include_github,
        graph=evidence_graph,
    )
    readiness = source_readiness(
        start=start_date,
        end=end_date,
        include_heavy_counts=effective_mode != "local-fast",
        include_github_frontier=include_github,
        include_analysis_inventory=effective_mode != "local-fast",
    )
    return CurrentStateEvidencePack(
        start=start,
        end=end,
        generated_at=datetime.now(timezone.utc),
        inventory=inventory,
        polylogue_readiness=archive_readiness(),
        evidence_graph=evidence_graph,
        source_readiness=readiness,
        work_correlations=correlations,
        correlation_summary=summarize_work_correlations(correlations),
        movement=movement_summary(
            start=start_date,
            end=end_date,
            rows=correlations,
            include_github_context=include_github,
        ),
        github_frontiers=project_github_frontier(inventory) if include_github else (),
    )


def inventory_markdown(items: Sequence[ProjectInventoryItem]) -> str:
    lines = [
        "| Project | Branch | Default | Dirty | Ahead/Behind | Last Commit | GitHub |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for item in items:
        dirty = "yes" if item.dirty else "no"
        ahead_behind = _ahead_behind(item)
        last = item.last_commit_at.isoformat(timespec="minutes") if item.last_commit_at else ""
        subject = (item.last_commit_subject or "").replace("|", "\\|")
        if subject:
            last = f"{last}<br>{subject}" if last else subject
        lines.append(
            "| {name} | {branch} | {default} | {dirty} | {ahead_behind} | {last} | {github} |".format(
                name=item.name,
                branch=item.branch or "",
                default=item.default_branch or "",
                dirty=dirty,
                ahead_behind=ahead_behind,
                last=last,
                github=item.github_slug or "",
            )
        )
    return "\n".join(lines)


def github_frontier_markdown(frontiers: Sequence[ProjectGitHubFrontier]) -> str:
    lines = [
        "| Project | Item | State | Lifecycle | Comments | Reviews | Title |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for frontier in frontiers:
        if not frontier.items:
            title = frontier.reason or "no items"
            lines.append(f"| {frontier.project} |  | {frontier.status} |  | 0 | 0 | {title} |")
            continue
        for item in frontier.items:
            title = item.title.replace("|", "\\|")
            ident = f"{item.kind} #{item.number}"
            if item.url:
                ident = f"[{ident}]({item.url})"
            reviews = _review_summary(item)
            lines.append(
                f"| {item.project} | {ident} | {item.state} | {item.lifecycle.lifecycle} | {item.comment_count} | {reviews} | {title} |"
            )
    return "\n".join(lines)


def github_frontier_summary_markdown(frontiers: Sequence[ProjectGitHubFrontier]) -> str:
    lines = [
        "| Project | Items | Open Frontier | Tracking/Horizon | Folded | Retired | Executed | Misframed | Unclear | Status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for frontier in frontiers:
        counts: Counter[str] = Counter(item.lifecycle.lifecycle for item in frontier.items)
        lines.append(
            "| {project} | {items} | {open_frontier} | {tracking} | {folded} | {retired} | {executed} | {misframed} | {unclear} | {status} |".format(
                project=frontier.project,
                items=len(frontier.items),
                open_frontier=counts["open_frontier"],
                tracking=counts["tracking_or_horizon"],
                folded=counts["folded_or_consolidated"],
                retired=counts["retired_stale"],
                executed=counts["executed"] + counts["pr_closed"],
                misframed=counts["misframed"],
                unclear=counts["unclear"],
                status=frontier.status,
            )
        )
    return "\n".join(lines)


def evidence_pack_markdown(pack: CurrentStateEvidencePack) -> str:
    readiness = pack.polylogue_readiness
    strongest_rows = strongest_work_correlations(pack.work_correlations)
    lines = [
        f"# Current-State Evidence Pack ({pack.start.date().isoformat()} → {pack.end.date().isoformat()})",
        "",
        "## Readiness",
        "",
        f"- Polylogue: `{readiness.status}` — {readiness.reason}",
        f"- Polylogue conversations: {readiness.conversation_count}",
        f"- Polylogue session products: {readiness.session_profile_count}",
        f"- Polylogue day products: {readiness.day_summary_count}",
        f"- Polylogue work-event products: {readiness.work_event_count}",
        "",
        "## Source Readiness",
        "",
        render_source_readiness(pack.source_readiness),
        "",
        "## Evidence Graph",
        "",
        render_evidence_graph_summary(pack.evidence_graph),
        "",
        "## Analysis Products",
        "",
        analysis_products_markdown(pack.evidence_graph),
        "",
        "## Analysis Claims",
        "",
        analysis_claims_markdown(pack.evidence_graph),
        "",
        "## Correlation Coverage",
        "",
        render_work_correlation_summary(pack.correlation_summary),
        "",
        "## Movement Summary",
        "",
        render_movement_summary(pack.movement),
        "",
        "## Strongest Correlated Rows",
        "",
        render_work_day_correlations(strongest_rows) if strongest_rows else "_No correlated work rows._",
    ]
    if pack.github_frontiers:
        lines.extend(
            [
                "",
                "## GitHub Frontier",
                "",
                github_frontier_summary_markdown(pack.github_frontiers),
                "",
                "### Frontier Items",
                "",
                github_frontier_markdown(pack.github_frontiers),
            ]
        )
    lines.extend([
        "",
        "## Active Inventory",
        "",
        inventory_markdown(pack.inventory),
    ])
    return "\n".join(lines)


def analysis_products_markdown(graph: EvidenceGraph) -> str:
    rows = tuple(node for node in graph.nodes if node.kind == "analysis_artifact")
    if not rows:
        return "_No generated analysis artifacts were surfaced._"
    grouped: dict[str, list[EvidenceNode]] = {}
    for node in rows:
        payload = node.payload or {}
        artifact = str(payload.get("name") or node.summary)
        grouped.setdefault(artifact, []).append(node)
    lines = [
        "| Artifact | Projects | Kind | Generated | Brief | Keys |",
        "|---|---|---|---|---|---|",
    ]
    for artifact, nodes in sorted(grouped.items()):
        node = nodes[0]
        payload = node.payload or {}
        keys = payload.get("top_level_keys") or ()
        generated = payload.get("generated_at") or ""
        artifact_cell = artifact.replace("|", "\\|")
        projects = ", ".join(sorted({item.project or "" for item in nodes if item.project}))
        brief = str(payload.get("brief") or "").replace("|", "\\|")
        lines.append(
            f"| {artifact_cell} | {projects} | {payload.get('kind') or ''} | {generated} | {brief} | {', '.join(keys)} |"
        )
    return "\n".join(lines)


def analysis_claims_markdown(graph: EvidenceGraph) -> str:
    rows = tuple(node for node in graph.nodes if node.kind == "analysis_claim")
    if not rows:
        return "_No generated analysis claims were surfaced._"
    lines = [
        "| Project | Type | Confidence | Claim |",
        "|---|---|---:|---|",
    ]
    for node in sorted(rows, key=lambda item: (item.project or "", item.summary)):
        payload = node.payload or {}
        claim_type = str(payload.get("claim_type") or "analysis_claim").replace("|", "\\|")
        confidence = payload.get("confidence")
        confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else ""
        summary = node.summary.replace("|", "\\|")
        lines.append(f"| {node.project or ''} | {claim_type} | {confidence_text} | {summary} |")
    return "\n".join(lines)


def _inventory_one(name: str, path: Path, active: bool) -> ProjectInventoryItem:
    exists = path.exists()
    is_git = (path / ".git").exists()
    if not exists or not is_git:
        return ProjectInventoryItem(name, path, exists, is_git, None, None, None, False, None, None, None, None, None, active)
    branch = _git(path, ["branch", "--show-current"]) or None
    default_branch = _default_branch(path)
    head = _git(path, ["rev-parse", "--short", "HEAD"]) or None
    dirty = bool(_git(path, ["status", "--porcelain"]))
    ahead, behind = _ahead_behind_counts(path)
    last_at, subject = _last_commit(path, default_branch or "HEAD")
    return ProjectInventoryItem(
        name=name,
        path=path,
        exists=exists,
        is_git_repo=is_git,
        branch=branch,
        default_branch=default_branch,
        head=head,
        dirty=dirty,
        ahead=ahead,
        behind=behind,
        last_commit_at=last_at,
        last_commit_subject=subject,
        github_slug=repo_slug(path),
        active_registry_entry=active,
    )


def _selected_project_inventory(selected: set[str]) -> tuple[ProjectInventoryItem, ...]:
    items: list[ProjectInventoryItem] = []
    for name in sorted(selected):
        entry = ALL_PROJECTS.get(name)
        items.append(_inventory_one(name, project_path(name), entry.active if entry else True))
    return tuple(items)


def _selected_projects(projects: Sequence[str] | None) -> set[str]:
    if not projects:
        return set()
    return {
        project
        for project in (canonical_project_name(value) for value in projects)
        if project is not None
    }


def _filter_inventory(
    inventory: Sequence[ProjectInventoryItem],
    *,
    selected: set[str],
) -> tuple[ProjectInventoryItem, ...]:
    if not selected:
        return tuple(inventory)
    return tuple(item for item in inventory if canonical_project_name(item.name) in selected)


def _is_inactive_path(path: Path) -> bool:
    return "_inactive" in path.parts


def _frontier_item(project: str, item: GitHubItem) -> ProjectGitHubFrontierItem:
    return ProjectGitHubFrontierItem(
        project=project,
        kind=item.kind,
        number=item.number,
        title=item.title,
        state=item.state,
        url=item.url,
        lifecycle=classify_lifecycle(item),
        label_names=tuple(label.name for label in item.labels),
        comment_count=len(item.comments),
        review_decision=item.review_decision,
        review_count=len(item.reviews),
        review_comment_count=len(item.review_comments),
    )


def _review_summary(item: ProjectGitHubFrontierItem) -> str:
    total = item.review_count + item.review_comment_count
    if item.review_decision:
        return f"{total} ({item.review_decision})"
    return str(total)


def _default_branch(path: Path) -> str | None:
    remote_head = _git(path, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if remote_head:
        return remote_head.removeprefix("origin/")
    for candidate in ("master", "main"):
        if _git(path, ["rev-parse", "--verify", candidate]):
            return candidate
    return _git(path, ["branch", "--show-current"]) or None


def _ahead_behind_counts(path: Path) -> tuple[int | None, int | None]:
    upstream = _git(path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if not upstream:
        return None, None
    out = _git(path, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
    if not out:
        return None, None
    parts = out.split()
    if len(parts) != 2:
        return None, None
    return int(parts[0]), int(parts[1])


def _last_commit(path: Path, rev: str) -> tuple[datetime | None, str | None]:
    out = _git(path, ["log", "-1", "--format=%aI%x1f%s", rev])
    if not out:
        return None, None
    raw_dt, _, subject = out.partition("\x1f")
    try:
        return datetime.fromisoformat(raw_dt.replace("Z", "+00:00")), subject
    except ValueError:
        return None, subject or None


def _ahead_behind(item: ProjectInventoryItem) -> str:
    if item.ahead is None or item.behind is None:
        return ""
    return f"+{item.ahead}/-{item.behind}"


def _git(path: Path, args: Sequence[str]) -> str:
    return run_git(path, *args, timeout=15) or ""


__all__ = [
    "ProjectInventoryItem",
    "ProjectGitHubFrontier",
    "ProjectGitHubFrontierItem",
    "CurrentStateEvidencePack",
    "active_project_inventory",
    "current_state_evidence_pack",
    "evidence_pack_markdown",
    "github_frontier_markdown",
    "github_frontier_summary_markdown",
    "inventory_markdown",
    "project_github_frontier",
    "project_inventory",
]
