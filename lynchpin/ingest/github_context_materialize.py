"""Materialize GitHub lifecycle context into a canonical local product."""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.parse import parse_datetime
from ..core.source_contracts import GITHUB_CONTEXT_DEFAULT_MAX_AGE_SECONDS
from ..sources.git import commit_facts
from ..sources.github import (
    GitHubItemInventory,
    fetch_issue,
    fetch_issue_inventory,
    fetch_pr,
    fetch_pr_inventory,
    repo_slug,
)
from ..sources.github_context import (
    GITHUB_CONTEXT_SCHEMA_VERSION,
    active_github_repos,
    github_context_path,
    github_item_to_payload,
)
from ._manifest import write_manifest

log = logging.getLogger(__name__)

DEFAULT_GITHUB_LIST_LIMIT = 10_000


def materialize_github_context(
    *,
    output: Path | None = None,
    start: date | None = None,
    end: date | None = None,
    open_limit: int = DEFAULT_GITHUB_LIST_LIMIT,
    closed_limit: int = DEFAULT_GITHUB_LIST_LIMIT,
    closed_pr_limit: int = DEFAULT_GITHUB_LIST_LIMIT,
    commit_ref_fetch_limit: int = 20,
    projects: set[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    output = output or github_context_path()
    if (start is None) != (end is None):
        raise MaterializationError("github_context_materialize", reason="GitHub context materialization requires both start and end")
    if start is None or end is None:
        end = datetime.now(timezone.utc).date() + timedelta(days=1)
        start = end - timedelta(days=90)
    if end <= start:
        raise MaterializationError("github_context_materialize", reason="GitHub context materialization end must be after start")

    rows: dict[tuple[str, str, int], dict[str, Any]] = _load_existing_rows(output)
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    detail_refreshes = 0
    detail_reuses = 0
    detail_misses = 0
    inventory_items_seen = 0
    missing_commit_refs_seen = 0
    missing_commit_refs_attempted = 0
    missing_commit_refs_fetched = 0
    missing_commit_refs_deferred = 0

    active_paths = _active_repo_paths()
    if projects is not None:
        active_paths = {project: path for project, path in active_paths.items() if project in projects}
    for project, path in active_paths.items():
        slug = repo_slug(path)
        if slug is None:
            continue
        if progress is not None:
            progress(f"GitHub context: refreshing {project} ({slug})")
        inventories = [
            ("issue", "all", lambda: fetch_issue_inventory(path, state="all", limit=max(open_limit, closed_limit), use_cache=False)),
            ("pr", "all", lambda: fetch_pr_inventory(path, state="all", limit=max(open_limit, closed_pr_limit), use_cache=False)),
        ]
        for kind, state, refresh in inventories:
            if progress is not None:
                progress(f"GitHub context: fetching {project} {kind}s {state} inventory")
            result = refresh()
            statuses[result.status] += 1
            if result.reason:
                reasons[result.reason] += 1
            if result.status != "ok":
                continue
            inventory_items_seen += len(result.items)
            _reconcile_current_open_rows(rows, project=project, kind=kind, items=result.items)
            for inventory in result.items:
                key = (project, inventory.kind, inventory.number)
                existing = rows.get(key)
                if existing is not None and not _inventory_requires_detail(existing, inventory):
                    detail_reuses += 1
                    _merge_inventory_metadata(existing, inventory)
                    continue
                if progress is not None:
                    progress(f"GitHub context: hydrating {project} {inventory.kind} #{inventory.number}")
                if inventory.kind == "pr":
                    item = fetch_pr(path, inventory.number, use_cache=False, include_review_comments=True)
                else:
                    item = fetch_issue(path, inventory.number, use_cache=False)
                if item is None:
                    detail_misses += 1
                    continue
                detail_refreshes += 1
                rows[key] = github_item_to_payload(project=project, item=item)

    for fact in commit_facts(start=start, end=end, include_paths=False):
        project = fact.repo
        path = active_paths.get(project)
        if path is None:
            continue
        from ..sources.github import extract_commit_refs

        refs = extract_commit_refs(fact.subject)
        for number in sorted(refs["prs"]):
            if (project, "pr", number) in rows:
                continue
            missing_commit_refs_seen += 1
            if missing_commit_refs_attempted >= commit_ref_fetch_limit:
                missing_commit_refs_deferred += 1
                continue
            missing_commit_refs_attempted += 1
            item = fetch_pr(path, number, use_cache=False, include_review_comments=True)
            if item is not None:
                rows[(project, "pr", number)] = github_item_to_payload(project=project, item=item)
                missing_commit_refs_fetched += 1
        for number in sorted(refs["issues"] - refs["prs"]):
            if (project, "issue", number) in rows:
                continue
            missing_commit_refs_seen += 1
            if missing_commit_refs_attempted >= commit_ref_fetch_limit:
                missing_commit_refs_deferred += 1
                continue
            missing_commit_refs_attempted += 1
            item = fetch_issue(path, number, use_cache=False)
            if item is not None:
                rows[(project, "issue", number)] = github_item_to_payload(project=project, item=item)
                missing_commit_refs_fetched += 1

    ordered = [rows[key] for key in sorted(rows)]
    _raise_for_failed_refresh(statuses=statuses, reasons=reasons, active_project_count=len(active_paths))
    _raise_for_missing_detail_fetches(detail_misses)

    input_files = _repo_git_inputs(active_paths)
    manifest = {
        "dataset": "lynchpin.github_context",
        "schema_version": GITHUB_CONTEXT_SCHEMA_VERSION,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": len(ordered),
        "first_date": start.isoformat(),
        "last_date": (end - timedelta(days=1)).isoformat(),
        "covered_dates": [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days)
        ],
        "covered_date_count": (end - start).days,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_semantics": "start inclusive, end exclusive",
        "ttl_seconds": GITHUB_CONTEXT_DEFAULT_MAX_AGE_SECONDS,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "fetch_status_counts": dict(statuses),
        "fetch_reason_counts": dict(reasons),
        "inventory_items_seen": inventory_items_seen,
        "detail_refreshes": detail_refreshes,
        "detail_reuses": detail_reuses,
        "detail_misses": detail_misses,
        "missing_commit_refs_seen": missing_commit_refs_seen,
        "missing_commit_refs_attempted": missing_commit_refs_attempted,
        "missing_commit_refs_fetched": missing_commit_refs_fetched,
        "missing_commit_refs_deferred": missing_commit_refs_deferred,
        "commit_ref_fetch_limit": commit_ref_fetch_limit,
        "project_counts": dict(Counter(str(row["project"]) for row in ordered)),
    }
    _write_product(output=output, rows=ordered, manifest=manifest)

    try:
        substrate_rows = _promote_github_context_to_substrate(output)
        manifest["substrate_rows"] = substrate_rows
    except Exception as exc:
        log.warning("github_context substrate promotion failed: %s", exc)
        manifest["substrate_rows"] = 0

    return manifest


def _active_repo_paths() -> dict[str, Path]:
    return active_github_repos()


def _load_existing_rows(output: Path) -> dict[tuple[str, str, int], dict[str, Any]]:
    rows: dict[tuple[str, str, int], dict[str, Any]] = {}
    if not output.exists():
        return rows
    with output.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            project = str(row.get("project") or "")
            kind = str(row.get("kind") or "")
            try:
                number = int(row.get("number") or 0)
            except (TypeError, ValueError):
                continue
            if project and kind in {"issue", "pr"} and number:
                rows[(project, kind, number)] = row
    return rows


def _reconcile_current_open_rows(
    rows: dict[tuple[str, str, int], dict[str, Any]],
    *,
    project: str,
    kind: str,
    items: tuple[Any, ...],
) -> None:
    if any(item.state != "open" for item in items):
        open_items = [item for item in items if item.state == "open"]
    else:
        open_items = list(items)
    live_open_numbers = {int(item.number) for item in open_items}
    stale_keys = [
        key
        for key, row in rows.items()
        if key[0] == project
        and key[1] == kind
        and str(row.get("state") or "").lower() == "open"
        and key[2] not in live_open_numbers
    ]
    for key in stale_keys:
        del rows[key]


def _inventory_requires_detail(existing: dict[str, Any], inventory: GitHubItemInventory) -> bool:
    if str(existing.get("kind") or "") != inventory.kind:
        return True
    if str(existing.get("state") or "").lower() != inventory.state:
        return True
    existing_updated = _payload_datetime(existing.get("updated_at"))
    if inventory.updated_at is None or existing_updated is None:
        return True
    if inventory.updated_at > existing_updated:
        return True
    if inventory.kind == "pr" and inventory.state == "open" and not _can_reuse_existing_pr(existing, inventory):
        return True
    return False


def _merge_inventory_metadata(existing: dict[str, Any], inventory: GitHubItemInventory) -> None:
    existing["repo"] = existing.get("repo") or inventory.repo
    existing["slug"] = existing.get("slug") or inventory.slug
    existing["kind"] = inventory.kind
    existing["number"] = inventory.number
    existing["state"] = inventory.state
    if inventory.updated_at is not None:
        existing["updated_at"] = inventory.updated_at.isoformat()
    if inventory.closed_at is not None:
        existing["closed_at"] = inventory.closed_at.isoformat()
    if inventory.merged_at is not None:
        existing["merged_at"] = inventory.merged_at.isoformat()


def _payload_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return parse_datetime(value)
    return None


def _can_reuse_existing_pr(existing: dict[str, Any] | None, listed_item) -> bool:
    if existing is None:
        return False
    state = str(existing.get("state") or listed_item.state or "").lower()
    if state == "open" or listed_item.state == "open":
        return False
    return bool(existing.get("review_comments") or existing.get("reviews") or existing.get("comments"))


def _repo_git_inputs(active_paths: dict[str, Path] | None = None) -> tuple[Path, ...]:
    active_paths = active_paths or _active_repo_paths()
    inputs: list[Path] = []
    for path in active_paths.values():
        git_dir = path / ".git"
        for candidate in (git_dir / "HEAD", git_dir / "logs/HEAD", git_dir / "packed-refs"):
            if candidate.exists():
                inputs.append(candidate)
    return tuple(inputs)


def _raise_for_failed_refresh(
    *,
    statuses: Counter[str],
    reasons: Counter[str],
    active_project_count: int,
) -> None:
    if active_project_count == 0:
        return
    failures = statuses.get("error", 0) + statuses.get("unavailable", 0)
    if failures == 0:
        return
    reason_parts = [f"{status}={count}" for status, count in sorted(statuses.items())]
    reason_detail = ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))
    reason = "GitHub network refresh failed"
    if reason_parts:
        reason = f"{reason}: {', '.join(reason_parts)}"
    if reason_detail:
        reason = f"{reason}; {reason_detail}"
    raise MaterializationError("github_context", reason=reason)


def _raise_for_missing_detail_fetches(detail_misses: int) -> None:
    if detail_misses == 0:
        return
    raise MaterializationError(
        "github_context",
        reason=f"GitHub detail refresh failed for {detail_misses} item(s); existing product preserved",
    )


def _write_product(
    *,
    output: Path,
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_name(f".{output.name}.tmp")
    tmp_manifest = output.with_suffix(".manifest.json.tmp")
    with tmp_output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    write_manifest(tmp_manifest, manifest)
    tmp_output.replace(output)
    tmp_manifest.replace(output.with_suffix(".manifest.json"))


def _promote_github_context_to_substrate(ndjson_path: Path) -> int:
    """Promote the written NDJSON product into the 6 github_* DuckDB tables.

    Reads the NDJSON without triggering re-materialization (ensure=False).
    Returns total rows inserted across all 6 tables.
    """
    from ..sources.github_context import iter_github_context
    from ..substrate.connection import connect, update_read_snapshot
    from ..substrate.github import (
        promote_github_issue_comments,
        promote_github_issues,
        promote_github_pr_comments,
        promote_github_pr_review_comments,
        promote_github_pr_reviews,
        promote_github_prs,
    )

    issue_rows: list[dict[str, Any]] = []
    issue_comment_rows: list[dict[str, Any]] = []
    pr_rows: list[dict[str, Any]] = []
    pr_comment_rows: list[dict[str, Any]] = []
    pr_review_rows: list[dict[str, Any]] = []
    pr_review_comment_rows: list[dict[str, Any]] = []

    for ctx_row in iter_github_context(path=ndjson_path, ensure=False):
        project = ctx_row.project
        item = ctx_row.item

        if item.kind == "issue":
            issue_rows.append({
                "project": project,
                "number": item.number,
                "title": item.title,
                "body": item.body,
                "state": item.state,
                "author": item.author.login,
                "labels": [label.name for label in item.labels],
                "comment_count": len(item.comments),
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                "closed_at": item.closed_at.isoformat() if item.closed_at else None,
                "url": item.url,
            })
            for idx, comment in enumerate(item.comments):
                issue_comment_rows.append({
                    "project": project,
                    "issue_number": item.number,
                    "comment_idx": idx,
                    "author": comment.author.login,
                    "body": comment.body,
                    "created_at": comment.created_at.isoformat() if comment.created_at else None,
                    "url": comment.url,
                })

        elif item.kind == "pr":
            pr_rows.append({
                "project": project,
                "number": item.number,
                "title": item.title,
                "body": item.body,
                "state": item.state,
                "author": item.author.login,
                "labels": [label.name for label in item.labels],
                "merge_commit": item.merge_commit,
                "review_decision": item.review_decision,
                "comment_count": len(item.comments),
                "review_count": len(item.reviews),
                "review_comment_count": len(item.review_comments),
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                "closed_at": item.closed_at.isoformat() if item.closed_at else None,
                "merged_at": item.merged_at.isoformat() if item.merged_at else None,
                "url": item.url,
            })
            for idx, comment in enumerate(item.comments):
                pr_comment_rows.append({
                    "project": project,
                    "pr_number": item.number,
                    "comment_idx": idx,
                    "author": comment.author.login,
                    "body": comment.body,
                    "created_at": comment.created_at.isoformat() if comment.created_at else None,
                    "url": comment.url,
                })
            for idx, review in enumerate(item.reviews):
                pr_review_rows.append({
                    "project": project,
                    "pr_number": item.number,
                    "review_idx": idx,
                    "author": review.author.login,
                    "state": review.state,
                    "body": review.body,
                    "submitted_at": review.submitted_at.isoformat() if review.submitted_at else None,
                    "url": review.url,
                })
            for idx, rc in enumerate(item.review_comments):
                pr_review_comment_rows.append({
                    "project": project,
                    "pr_number": item.number,
                    "comment_idx": idx,
                    "author": rc.author.login,
                    "body": rc.body,
                    "path": rc.path,
                    "line": rc.line,
                    "diff_hunk": rc.diff_hunk,
                    "created_at": rc.created_at.isoformat() if rc.created_at else None,
                    "url": rc.url,
                })

    with connect() as conn:
        n = 0
        n += promote_github_issues(conn, rows=issue_rows)
        n += promote_github_issue_comments(conn, rows=issue_comment_rows)
        n += promote_github_prs(conn, rows=pr_rows)
        n += promote_github_pr_comments(conn, rows=pr_comment_rows)
        n += promote_github_pr_reviews(conn, rows=pr_review_rows)
        n += promote_github_pr_review_comments(conn, rows=pr_review_comment_rows)

    update_read_snapshot()
    log.info(
        "github_context substrate: %d issues, %d issue_comments, %d prs, "
        "%d pr_comments, %d reviews, %d review_comments",
        len(issue_rows), len(issue_comment_rows), len(pr_rows),
        len(pr_comment_rows), len(pr_review_rows), len(pr_review_comment_rows),
    )
    return n
