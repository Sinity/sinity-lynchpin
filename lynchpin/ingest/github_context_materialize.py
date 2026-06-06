"""Materialize GitHub lifecycle context into a canonical local product."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.git import commit_facts
from ..sources.github import GITHUB_CACHE_TTL_SECONDS, fetch_issue, fetch_issues, fetch_pr, fetch_prs, repo_slug
from ..sources.github_context import (
    GITHUB_CONTEXT_SCHEMA_VERSION,
    github_context_path,
    github_item_to_payload,
)


def materialize_github_context(
    *,
    output: Path | None = None,
    start: date | None = None,
    end: date | None = None,
    open_limit: int = 100,
    closed_limit: int = 40,
    closed_pr_limit: int = 40,
) -> dict[str, Any]:
    output = output or github_context_path()
    if (start is None) != (end is None):
        raise ValueError("GitHub context materialization requires both start and end")
    if start is None or end is None:
        end = datetime.now(timezone.utc).date() + timedelta(days=1)
        start = end - timedelta(days=90)
    if end <= start:
        raise ValueError("GitHub context materialization end must be after start")

    rows: dict[tuple[str, str, int], dict[str, Any]] = {}
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    active_paths = _active_repo_paths()
    for project, path in active_paths.items():
        slug = repo_slug(path)
        if slug is None:
            continue
        for result in (
            fetch_issues(path, state="open", limit=open_limit, use_cache=False),
            fetch_issues(path, state="closed", limit=closed_limit, use_cache=False),
            fetch_prs(path, state="open", limit=open_limit, use_cache=False),
            fetch_prs(path, state="closed", limit=closed_pr_limit, use_cache=False),
        ):
            statuses[result.status] += 1
            if result.reason:
                reasons[result.reason] += 1
            for item in result.items:
                if item.kind == "pr":
                    item = fetch_pr(path, item.number, use_cache=False, include_review_comments=True) or item
                rows[(project, item.kind, item.number)] = github_item_to_payload(project=project, item=item)

    for fact in commit_facts(start=start, end=end, include_paths=False):
        project = fact.repo
        path = active_paths.get(project)
        if path is None:
            continue
        from ..sources.github import extract_commit_refs

        refs = extract_commit_refs(fact.subject)
        for number in sorted(refs["prs"]):
            item = fetch_pr(path, number, use_cache=False, include_review_comments=True, max_age_seconds=GITHUB_CACHE_TTL_SECONDS)
            if item is not None:
                rows[(project, "pr", number)] = github_item_to_payload(project=project, item=item)
        for number in sorted(refs["issues"] - refs["prs"]):
            item = fetch_issue(path, number, use_cache=False, max_age_seconds=GITHUB_CACHE_TTL_SECONDS)
            if item is not None:
                rows[(project, "issue", number)] = github_item_to_payload(project=project, item=item)

    ordered = [rows[key] for key in sorted(rows)]
    _raise_for_failed_refresh(statuses=statuses, reasons=reasons, active_project_count=len(active_paths))

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
        "ttl_seconds": GITHUB_CACHE_TTL_SECONDS,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "fetch_status_counts": dict(statuses),
        "fetch_reason_counts": dict(reasons),
        "project_counts": dict(Counter(str(row["project"]) for row in ordered)),
    }
    _write_product(output=output, rows=ordered, manifest=manifest)
    return manifest


def _active_repo_paths() -> dict[str, Path]:
    from ..graph.current_state import active_project_inventory

    return {
        item.name: item.path
        for item in active_project_inventory()
        if item.exists and item.is_git_repo and item.github_slug is not None
    }


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
    tmp_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_output.replace(output)
    tmp_manifest.replace(output.with_suffix(".manifest.json"))
