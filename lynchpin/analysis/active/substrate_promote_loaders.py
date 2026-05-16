"""Source loaders for substrate promotion."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_commit_facts(path: str) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Hydrate active_commit_facts.json → (facts, annotations).

    Returns (Iterable[GitCommitFact], dict[str, dict]) where annotations
    maps commit sha → enrichment fields from the JSON (conventional_*,
    github_refs, categories, change_types, classified_files_changed,
    parent_count, default_branch, head).

    Line counts are zero (churn_caveat: not present in active facts).
    """
    from lynchpin.sources.git import GitCommitFact

    p = Path(path)
    if not p.exists():
        return [], {}
    with p.open() as f:
        data = json.load(f)
    facts: list[GitCommitFact] = []
    annotations: dict[str, dict[str, Any]] = {}
    for entry in data.get("commits", []):
        sha = entry.get("sha") or ""
        ts_raw = entry.get("timestamp") or ""
        try:
            authored_at = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue
        path_roots_raw = entry.get("path_roots") or {}
        path_roots_tuple: tuple[str, ...] = (
            tuple(path_roots_raw.keys())
            if isinstance(path_roots_raw, dict)
            else tuple(path_roots_raw)
        )
        facts.append(
            GitCommitFact(
                repo=entry.get("project") or "",
                commit=sha,
                authored_at=authored_at,
                author=entry.get("author") or "",
                subject=entry.get("subject") or "",
                lines_added=0,
                lines_deleted=0,
                lines_changed=0,
                files_changed=int(entry.get("files_changed") or 0),
                paths=tuple(entry.get("paths") or ()),
                path_roots=path_roots_tuple,
            )
        )
        annotations[sha] = {
            "conventional_kind": entry.get("conventional_kind"),
            "conventional_scope": entry.get("conventional_scope"),
            "conventional_signature": entry.get("conventional_signature"),
            "breaking_change": entry.get("breaking_change", False),
            "github_refs": entry.get("github_refs"),
            "categories": entry.get("categories"),
            "change_types": entry.get("change_types"),
            "classified_files_changed": entry.get("classified_files_changed"),
            "parent_count": entry.get("parent_count"),
            "default_branch": entry.get("default_branch"),
            "head": entry.get("head"),
        }
    return facts, annotations


def _merge_ai_attribution(
    annotations: dict[str, dict[str, Any]],
    path: str | None,
) -> None:
    """Merge active_ai_attribution.json high/medium rows into commit annotations."""
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for row in data.get("commits", []):
        if not isinstance(row, dict):
            continue
        sha = row.get("sha")
        attribution = row.get("ai_attribution")
        if not sha or attribution not in {"high", "medium"}:
            continue
        annotations.setdefault(str(sha), {})["ai_attribution"] = {
            "classification": attribution,
            "supporting_session_ids": list(row.get("supporting_session_ids") or []),
            "supporting_providers": list(row.get("supporting_providers") or []),
            "supporting_session_count": int(row.get("supporting_session_count") or 0),
            "matched_via": "polylogue_session_project_day",
        }


def _load_file_change_facts(
    path: str,
) -> tuple[list[Any], dict[tuple[str, str], dict[str, Any]]]:
    """Hydrate active_file_change_facts.json → (facts, annotations).

    Returns (Iterable[GitFileChangeFact], dict[(sha, path), dict]) where
    annotations maps (sha, path) → {change_type, status_code, previous_path}.
    """
    from lynchpin.sources.git import GitFileChangeFact

    p = Path(path)
    if not p.exists():
        return [], {}
    with p.open() as f:
        data = json.load(f)
    facts: list[GitFileChangeFact] = []
    annotations: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in data.get("file_changes", []):
        sha = entry.get("sha") or ""
        fpath = entry.get("path") or ""
        ts_raw = entry.get("timestamp") or ""
        try:
            authored_at = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError):
            continue
        facts.append(
            GitFileChangeFact(
                repo=entry.get("project") or "",
                commit=sha,
                authored_at=authored_at,
                path=fpath,
                path_root=entry.get("path_root") or "",
                lines_added=0,
                lines_deleted=0,
                lines_changed=0,
            )
        )
        annotations[(sha, fpath)] = {
            "change_type": entry.get("change_type"),
            "status_code": entry.get("status_code"),
            "previous_path": entry.get("previous_path"),
        }
    return facts, annotations


def _load_symbol_change_rows(path: str) -> Iterator[dict[str, Any]]:
    """Yield active_symbol_changes.json events as dict rows."""
    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("events", []):
        if isinstance(entry, dict):
            yield entry


def _load_pr_review_rows(path: str) -> Iterator[dict[str, Any]]:
    """Yield active_pr_review_topology.json prs as dict rows."""
    p = Path(path)
    if not p.exists():
        return
    with p.open() as f:
        data = json.load(f)
    for entry in data.get("prs", []):
        if isinstance(entry, dict):
            yield entry
