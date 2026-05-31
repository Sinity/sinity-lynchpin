"""Active GitHub lifecycle frontier materializer.

Produces active_github_frontier.json: open issues/PRs with lifecycle
classification, recently closed items, and work-package cross-references.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ...sources.github import (
    GitHubItem,
    GitHubItemKind,
    GitHubLifecycleClassification,
    classify_lifecycle,
    fetch_issues,
    fetch_prs,
)
from lynchpin.core.io import load_json_if_exists, resolve_analysis_path, save_json

log = logging.getLogger(__name__)

_RECENT_CLOSED_DAYS = 30


@dataclass
class _FrontierItem:
    kind: GitHubItemKind
    number: int
    title: str
    state: str
    url: str | None
    author: str | None
    labels: tuple[str, ...]
    created_at: str | None
    updated_at: str | None
    closed_at: str | None
    merged_at: str | None = None
    lifecycle: str = "unclear"
    lifecycle_confidence: float = 0.0
    lifecycle_reasons: tuple[str, ...] = ()
    linked_packages: list[str] = field(default_factory=list)
    comment_count: int = 0
    inactivity_days: int | None = None
    inactivity_bucket: str = "unknown"
    caveats: list[str] = field(default_factory=list)
    # Arc B.4: kind-based intent vs execution hint. Set on open issues when
    # the project's recent AI-work-event mix is dominated by planning /
    # research with no implementation / testing observed — distinguishing
    # "still in intent stage" from "stalled execution."
    lifecycle_hint: str | None = None
    lifecycle_hint_reasons: tuple[str, ...] = ()


def build_active_github_frontier(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    work_packages_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build the active GitHub lifecycle frontier across active projects."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    snapshot_payload = _load_payload(snapshot_file or resolve_analysis_path("active_project_snapshot.json"))
    work_payload = _load_payload(work_packages_file or resolve_analysis_path("active_work_packages.json"))

    repos = _active_github_repos(snapshot_payload, selected=set(projects or ()))
    package_pr_map = _package_pr_map(work_payload)
    # Arc B.4: per-project AI kind mix in the same window. Lazily computed
    # only when there's at least one repo to classify, since
    # `work_day_correlations` over real archives is heavy.
    project_kind_mix: dict[str, dict[str, int]] | None = None

    project_rows: list[dict[str, Any]] = []
    for repo_path_str, project_name in sorted(repos.items()):
        repo_path = Path(repo_path_str)
        if not repo_path.is_dir():
            project_rows.append({
                "project": project_name,
                "path": repo_path_str,
                "status": "unavailable",
                "caveats": ["repository checkout not available on disk"],
            })
            continue

        open_issues = _fetch_and_classify(repo_path, "issue", "open", limit=80, project=project_name)
        open_prs = _fetch_and_classify(repo_path, "pr", "open", limit=40, project=project_name)
        closed_issues = _fetch_and_classify(
            repo_path, "issue", "closed", limit=40, project=project_name,
            since=end - timedelta(days=_RECENT_CLOSED_DAYS),
        )
        closed_prs = _fetch_and_classify(
            repo_path, "pr", "closed", limit=40, project=project_name,
            since=end - timedelta(days=_RECENT_CLOSED_DAYS),
        )

        all_items = open_issues + open_prs + closed_issues + closed_prs
        _link_packages(all_items, package_pr_map.get(project_name, {}))
        _annotate_inactivity(all_items, reference=end)
        if project_kind_mix is None:
            project_kind_mix = _project_kind_mix(start=start, end=end)
        _annotate_kind_hint(all_items, project_kind_mix.get(project_name))

        lifecycle_counts: Counter[str] = Counter()
        inactivity_counts: Counter[str] = Counter()
        for item in all_items:
            lifecycle_counts[item.lifecycle] += 1
            if item.state == "open":
                inactivity_counts[item.inactivity_bucket] += 1

        project_rows.append({
            "project": project_name,
            "path": repo_path_str,
            "status": "available",
            "item_count": len(all_items),
            "open_item_count": len(open_issues) + len(open_prs),
            "recently_closed_item_count": len(closed_issues) + len(closed_prs),
            "lifecycle_summary": dict(lifecycle_counts.most_common()),
            "open_inactivity_summary": dict(inactivity_counts.most_common()),
            "open_frontier_items": [
                _item_row(item) for item in all_items
                if item.lifecycle == "open_frontier"
            ],
            "recently_closed_items": [
                _item_row(item) for item in all_items
                if item.lifecycle not in ("open_frontier", "tracking_or_horizon")
                and item.state in ("closed", "merged")
            ],
            "tracking_or_horizon_items": [
                _item_row(item) for item in all_items
                if item.lifecycle == "tracking_or_horizon" and item.state == "open"
            ],
            "items_by_lifecycle": {
                lifecycle: [_item_row(item) for item in all_items if item.lifecycle == lifecycle]
                for lifecycle in sorted(set(item.lifecycle for item in all_items))
            },
            "caveats": _frontier_caveats(open_issues, open_prs),
        })

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "lifecycle_source": "lynchpin.sources.github.classify_lifecycle — heuristic over labels, body text, and comments",
            "caveat": "lifecycle classification is heuristic; treat as evidence, not ground truth",
            "closed_caveat": "closed issues may be retired, folded, or superseded — not necessarily executed",
            "package_linking": "work packages linked by PR/issue number overlap from commit refs",
        },
        "inputs": {
            "active_project_snapshot": str(snapshot_file or "active_project_snapshot.json"),
            "active_work_packages": str(work_packages_file or "active_work_packages.json"),
        },
        "projects": project_rows,
        "summary": _frontier_summary(project_rows),
    }


def run_active_github_frontier(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
    work_packages_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Materialize active GitHub frontier."""
    payload = build_active_github_frontier(
        start=start,
        end=end,
        projects=projects,
        snapshot_file=snapshot_file,
        work_packages_file=work_packages_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _active_github_repos(
    snapshot_payload: dict[str, Any] | None,
    *,
    selected: set[str],
) -> dict[str, str]:
    repos: dict[str, str] = {}
    projects = snapshot_payload.get("projects") if snapshot_payload else None
    if not isinstance(projects, list):
        return repos
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        path = str(row.get("path") or "")
        if not path or not Path(path).is_dir():
            continue
        repos[path] = project
    return repos


def _fetch_and_classify(
    repo_path: Path,
    kind: GitHubItemKind,
    state: str,
    *,
    limit: int,
    project: str,
    since: date | None = None,
) -> list[_FrontierItem]:
    if kind == "issue":
        result = fetch_issues(repo_path, state=state, limit=limit, use_cache=True)  # type: ignore[arg-type]
    else:
        result = fetch_prs(repo_path, state=state, limit=limit, use_cache=True)  # type: ignore[arg-type]

    if result.status != "ok":
        return []

    items: list[_FrontierItem] = []
    for item in result.items:
        if since is not None:
            item_date = item.closed_at or item.updated_at
            if item_date is not None and item_date.date() < since:
                continue
        classification = classify_lifecycle(item)
        items.append(_FrontierItem(
            kind=item.kind,
            number=item.number,
            title=item.title,
            state=item.state,
            url=item.url,
            author=item.author.login,
            labels=tuple(label.name for label in item.labels),
            created_at=item.created_at.isoformat() if item.created_at else None,
            updated_at=item.updated_at.isoformat() if item.updated_at else None,
            closed_at=item.closed_at.isoformat() if item.closed_at else None,
            merged_at=item.merged_at.isoformat() if item.merged_at else None,
            lifecycle=classification.lifecycle,
            lifecycle_confidence=classification.confidence,
            lifecycle_reasons=classification.reasons,
            comment_count=len(item.comments),
            caveats=_item_caveats(item, classification),
        ))
    return items


def _item_caveats(
    item: GitHubItem,
    classification: GitHubLifecycleClassification,
) -> list[str]:
    caveats: list[str] = []
    if classification.confidence < 0.7:
        caveats.append(f"low classification confidence ({classification.confidence:.0%})")
    if item.state == "closed" and classification.lifecycle not in ("executed", "pr_closed"):
        caveats.append("closed without execution evidence — may be retired, folded, or superseded")
    if item.kind == "pr" and item.state == "open":
        if item.review_decision == "CHANGES_REQUESTED":
            caveats.append("open PR with changes requested")
    return caveats


def _item_row(item: _FrontierItem) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "number": item.number,
        "title": item.title,
        "state": item.state,
        "url": item.url,
        "author": item.author,
        "labels": item.labels,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "closed_at": item.closed_at,
        "merged_at": item.merged_at,
        "lifecycle": item.lifecycle,
        "lifecycle_confidence": item.lifecycle_confidence,
        "lifecycle_reasons": item.lifecycle_reasons,
        "lifecycle_hint": item.lifecycle_hint,
        "lifecycle_hint_reasons": item.lifecycle_hint_reasons,
        "linked_packages": item.linked_packages,
        "comment_count": item.comment_count,
        "inactivity_days": item.inactivity_days,
        "inactivity_bucket": item.inactivity_bucket,
        "caveats": item.caveats,
    }


_INTENT_KINDS: frozenset[str] = frozenset({"planning", "research", "conversation"})
_EXECUTION_KINDS: frozenset[str] = frozenset({"implementation", "testing", "debugging", "refactoring"})


def _project_kind_mix(*, start: date, end: date) -> dict[str, dict[str, int]]:
    """Aggregate AI work-event kinds per project over the analysis window.

    Best-effort: if the correlation graph build fails (heavy/slow/missing
    sources) we silently return an empty dict so the frontier still renders.
    Caller treats absence as 'no hint available'.
    """
    try:
        from ...graph.work_correlation import work_day_correlations

        rows = work_day_correlations(start=start, end=end)
    except Exception:
        log.warning("work_day_correlations failed — AI kind mix unavailable for frontier")
        return {}
    mix: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = mix.setdefault(row.project, {})
        for kind, count in row.ai_kind_breakdown:
            bucket[kind] = bucket.get(kind, 0) + count
    return mix


def _annotate_kind_hint(
    items: list[_FrontierItem],
    project_kinds: dict[str, int] | None,
) -> None:
    """Mark open frontier issues whose project shows intent activity but no
    execution as `lifecycle_hint=design_or_open_loop`.

    Conservative: only fires on open issues classified open_frontier or
    tracking_or_horizon, when the project window has at least one intent-kind
    observation and zero execution-kind observations. Closed items are left
    alone (their state already encodes the conclusion).
    """
    if not project_kinds:
        return
    intent_count = sum(project_kinds.get(kind, 0) for kind in _INTENT_KINDS)
    execution_count = sum(project_kinds.get(kind, 0) for kind in _EXECUTION_KINDS)
    if intent_count == 0 or execution_count > 0:
        return
    for item in items:
        if item.state != "open":
            continue
        if item.lifecycle not in ("open_frontier", "tracking_or_horizon", "unclear"):
            continue
        item.lifecycle_hint = "design_or_open_loop"
        intent_summary = ", ".join(
            f"{kind}×{project_kinds[kind]}"
            for kind in _INTENT_KINDS
            if project_kinds.get(kind)
        )
        item.lifecycle_hint_reasons = (
            f"project AI mix is intent-only ({intent_summary}), no implementation/testing/debugging/refactoring observed",
        )


def _annotate_inactivity(items: list[_FrontierItem], *, reference: date) -> None:
    for item in items:
        last_seen = _parse_iso_date(item.updated_at) or _parse_iso_date(item.created_at)
        if last_seen is None:
            item.inactivity_days = None
            item.inactivity_bucket = "unknown"
            continue
        days = max(0, (reference - last_seen).days)
        item.inactivity_days = days
        item.inactivity_bucket = _bucket_inactivity(days)


def _bucket_inactivity(days: int) -> str:
    if days < 7:
        return "active"
    if days < 30:
        return "idle"
    if days < 90:
        return "dormant"
    return "stale"


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _package_pr_map(work_payload: dict[str, Any] | None) -> dict[str, dict[int, list[str]]]:
    """Build project -> PR_number -> [package_ids] map."""
    result: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    projects = work_payload.get("projects") if work_payload else None
    if not isinstance(projects, list):
        return dict(result)
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project:
            continue
        project_map = result[project]
        for package in row.get("packages") or ():
            if not isinstance(package, dict):
                continue
            package_id = str(package.get("work_package_id") or "")
            refs = package.get("refs") or {}
            pr_nums = refs.get("prs") if isinstance(refs, dict) else ()
            for pr_num in pr_nums or ():
                try:
                    project_map[int(pr_num)].append(package_id)
                except (TypeError, ValueError):
                    continue
    return dict(result)


def _link_packages(items: list[_FrontierItem], pr_map: dict[int, list[str]]) -> None:
    for item in items:
        if item.kind == "pr" and item.number in pr_map:
            item.linked_packages = pr_map[item.number]
        elif item.kind == "issue":
            for issue_ref in pr_map:
                pass


def _frontier_caveats(
    open_issues: list[_FrontierItem],
    open_prs: list[_FrontierItem],
) -> list[str]:
    caveats: list[str] = []
    if not open_issues and not open_prs:
        caveats.append("no open issues or PRs returned")
    low_confidence = sum(
        1 for item in open_issues + open_prs if item.lifecycle_confidence < 0.7
    )
    if low_confidence > 0:
        caveats.append(f"{low_confidence} open items have low classification confidence")
    return caveats


def _frontier_summary(project_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_open = 0
    total_recently_closed = 0
    all_lifecycle: Counter[str] = Counter()
    all_inactivity: Counter[str] = Counter()
    for row in project_rows:
        if row.get("status") != "available":
            continue
        total_open += int(row.get("open_item_count") or 0)
        total_recently_closed += int(row.get("recently_closed_item_count") or 0)
        lifecycle_summary = row.get("lifecycle_summary")
        if isinstance(lifecycle_summary, dict):
            all_lifecycle.update(lifecycle_summary)
        inactivity_summary = row.get("open_inactivity_summary")
        if isinstance(inactivity_summary, dict):
            all_inactivity.update(inactivity_summary)
    return {
        "available_project_count": sum(1 for r in project_rows if r.get("status") == "available"),
        "total_open_items": total_open,
        "total_recently_closed_items": total_recently_closed,
        "total_items": sum(int(r.get("item_count") or 0) for r in project_rows if r.get("status") == "available"),
        "lifecycle_distribution": dict(all_lifecycle.most_common()),
        "open_inactivity_distribution": dict(all_inactivity.most_common()),
    }


def _load_payload(path: str | PathLike[str]) -> dict[str, Any] | None:
    payload = load_json_if_exists(path)
    return payload if isinstance(payload, dict) else None


__all__ = ["build_active_github_frontier", "run_active_github_frontier"]
