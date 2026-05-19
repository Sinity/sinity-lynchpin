"""Commit table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_date_filter, add_in_filter, build_where
from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


def load_commit_facts(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    projects: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
) -> list[Any]:  # list[GitCommitFact]
    """SELECT and hydrate ``commit_fact`` rows to ``GitCommitFact`` instances.

    Filters compose with AND. All filters are optional.
    ``paths`` and ``path_roots`` (``VARCHAR[]``) are converted from list to
    tuple to match the frozen dataclass signature.
    """
    from lynchpin.sources.git import GitCommitFact

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("authored_at", start, end, clauses, params)
    add_in_filter("project", projects, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            sha, repo, project, authored_at, author, subject,
            lines_added, lines_deleted, lines_changed, files_changed,
            paths, path_roots
        FROM commit_fact
        {where}
        ORDER BY authored_at
    """
    rows = conn.execute(sql, params).fetchall()

    results: list[Any] = []
    for (
        sha,
        repo,
        project,
        authored_at,
        author,
        subject,
        lines_added,
        lines_deleted,
        lines_changed,
        files_changed,
        paths,
        path_roots,
    ) in rows:
        results.append(
            GitCommitFact(
                repo=repo,
                commit=sha,
                authored_at=authored_at,
                author=author or "",
                subject=subject or "",
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                lines_changed=lines_changed,
                files_changed=files_changed,
                paths=tuple(paths) if paths else (),
                path_roots=tuple(path_roots) if path_roots else (),
            )
        )
    return results


def read_commit_facts(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    projects: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Return a payload dict matching ``active_commit_facts.json`` shape.

    Queries ``commit_fact`` and wraps results in
    ``{"commits": [...], "projects": [...], "window": {...}}``
    so downstream consumers (ai_attribution, work_packages) see the same
    structure they get from the JSON file.
    """
    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("authored_at", start, end, clauses, params)
    add_in_filter("project", projects, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            sha, repo, project, authored_at, author, subject,
            lines_added, lines_deleted, lines_changed, files_changed,
            paths, path_roots, conventional_kind, conventional_scope,
            conventional_signature, github_refs, categories, change_types,
            classified_files_changed, parent_count, default_branch,
        FROM commit_fact
        {where}
        ORDER BY authored_at
    """
    rows = conn.execute(sql, params).fetchall()

    commits: list[dict[str, Any]] = []
    seen_projects: dict[str, dict[str, Any]] = {}
    actual_start: str | None = None
    actual_end: str | None = None

    for (
        sha,
        repo,
        project,
        authored_at,
        author,
        subject,
        lines_added,
        lines_deleted,
        lines_changed,
        files_changed,
        paths,
        path_roots,
        conv_kind,
        conv_scope,
        conv_signature,
        github_refs,
        categories,
        change_types,
        classified_files_changed,
        parent_count,
        default_branch,
    ) in rows:
        ts = (
            authored_at.isoformat()
            if isinstance(authored_at, datetime)
            else str(authored_at)
        )
        d = (
            authored_at.date().isoformat()
            if isinstance(authored_at, datetime)
            else ts[:10]
        )

        if actual_start is None or d < actual_start:
            actual_start = d
        if actual_end is None or d > actual_end:
            actual_end = d

        commits.append(
            {
                "project": project,
                "sha": sha,
                "short_sha": sha[:7],
                "timestamp": ts,
                "date": d,
                "subject": subject or "",
                "author": author or "",
                "conventional_kind": conv_kind or "other",
                "conventional_scope": conv_scope or "",
                "conventional_signature": conv_signature or "other",
                "paths": list(paths) if paths else [],
                "path_roots": list(path_roots) if path_roots else [],
                "categories": list(categories)
                if isinstance(categories, list)
                else (categories if categories else []),
                "github_refs": github_refs or {},
                "change_types": list(change_types)
                if isinstance(change_types, list)
                else (change_types if change_types else []),
                "classified_files_changed": classified_files_changed or 0,
                "lines_added": lines_added or 0,
                "lines_deleted": lines_deleted or 0,
                "lines_changed": lines_changed or 0,
                "files_changed": files_changed or 0,
                "default_branch": default_branch or "main",
            }
        )

        if project and project not in seen_projects:
            seen_projects[project] = {
                "project": project,
                "default_branch": default_branch or "main",
            }

    return {
        "commits": commits,
        "projects": list(seen_projects.values()),
        "window": {
            "start": actual_start or (start.isoformat() if start else ""),
            "end": actual_end or (end.isoformat() if end else ""),
        },
    }


# ---------------------------------------------------------------------------
# file_change_fact
# ---------------------------------------------------------------------------


_COMMIT_COLUMNS = (
    "sha", "repo", "project", "authored_at", "author", "subject",
    "lines_added", "lines_deleted", "lines_changed", "files_changed",
    "paths", "path_roots",
    "conventional_kind", "conventional_scope", "conventional_signature",
    "breaking_change", "github_refs", "ai_attribution",
    "categories", "change_types", "classified_files_changed",
    "parent_count", "default_branch", "head",
)


def promote_commits(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    facts: Iterable[Any],  # Iterable[GitCommitFact]
    project_lookup: Callable[[str], str | None] | None = None,
    annotations: Mapping[str, dict[str, Any]] | None = None,
) -> int:
    """INSERT commit rows, idempotent on refresh_id.  Returns rows written.

    ``project_lookup`` is called with the repo name; when omitted the repo name
    is used directly as project.  ``annotations`` is a mapping of commit sha →
    annotation dict (keys: conventional_kind, conventional_scope,
    conventional_signature, breaking_change, github_refs, ai_attribution,
    categories, change_types, classified_files_changed, parent_count,
    default_branch, head).
    """
    ann = annotations or {}

    def extract(f: Any) -> tuple[Any, ...]:
        proj = project_lookup(f.repo) if project_lookup else f.repo
        a = ann.get(f.commit, {})

        github_refs_raw = a.get("github_refs")
        # DuckDB accepts a Python dict for STRUCT columns; ignore non-dicts.
        github_refs = github_refs_raw if isinstance(github_refs_raw, dict) else None

        ai_attribution = a.get("ai_attribution")
        ai_attribution_json = json.dumps(ai_attribution) if ai_attribution is not None else None

        categories_raw = a.get("categories")
        categories_json = json.dumps(categories_raw) if isinstance(categories_raw, dict) else "{}"
        change_types_raw = a.get("change_types")
        change_types_json = json.dumps(change_types_raw) if isinstance(change_types_raw, dict) else "{}"

        classified_files = a.get("classified_files_changed")
        parent_count_val = a.get("parent_count")

        return (
            f.commit, f.repo, proj, f.authored_at, f.author, f.subject,
            f.lines_added, f.lines_deleted, f.lines_changed, f.files_changed,
            list(f.paths), list(f.path_roots),
            a.get("conventional_kind"),
            a.get("conventional_scope"),
            a.get("conventional_signature"),
            bool(a.get("breaking_change", False)),
            github_refs,
            ai_attribution_json,
            categories_json,
            change_types_json,
            int(classified_files) if classified_files is not None else 0,
            int(parent_count_val) if parent_count_val is not None else 1,
            a.get("default_branch"),
            a.get("head"),
        )

    return promote_rows(
        conn,
        table="commit_fact",
        columns=_COMMIT_COLUMNS,
        refresh_id=refresh_id,
        rows=facts,
        extractor=extract,
    )


# ── file_change_fact ──────────────────────────────────────────────────────────

__all__ = ["load_commit_facts", "promote_commits", "read_commit_facts"]
