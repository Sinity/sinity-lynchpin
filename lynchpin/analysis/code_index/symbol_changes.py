"""Symbol-change correlation across active project commits.

Joins ``active_symbol_index.json`` and ``active_file_change_facts.json`` to
answer: which symbols sit in files touched by each recent commit, and which of
those are exported (so a removal/rename is a candidate breaking change)?

Path-level intersection only. Line-range intersection would require running
``git diff`` per commit-path, which is intentionally deferred — see caveats in
the output payload.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from typing import Any, Sequence

from ..core.io import load_json_if_exists, resolve_analysis_path, save_json


def build_active_symbol_changes(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    symbol_index_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    index_payload = _dict(load_json_if_exists(
        symbol_index_file or resolve_analysis_path("active_symbol_index.json")))
    changes_payload = _dict(load_json_if_exists(
        file_changes_file or resolve_analysis_path("active_file_change_facts.json")))

    selected = set(projects or ())
    symbols_by_path = _index_symbols_by_path(index_payload, selected)
    file_changes = _filter_changes(changes_payload, selected)

    project_breaking: dict[str, list[dict[str, Any]]] = defaultdict(list)
    project_touches: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    project_commit_count: dict[str, set[str]] = defaultdict(set)
    rows: list[dict[str, Any]] = []

    for change in file_changes:
        project = change.get("project")
        if not project:
            continue
        path = change.get("path") or ""
        change_type = (change.get("change_type") or change.get("status_code") or "").upper()
        symbols = symbols_by_path.get((project, path), ())
        project_commit_count[project].add(change.get("sha") or "")
        for sym in symbols:
            kind = sym.get("symbol_kind") or "unknown"
            qual = sym.get("qualified_name") or ""
            exported = bool(sym.get("exported"))
            project_touches[project][kind] += 1
            breaking = exported and change_type and change_type[0] in {"D", "R"}
            row = {
                "project": project,
                "sha": change.get("sha"),
                "short_sha": change.get("short_sha"),
                "date": change.get("date"),
                "path": path,
                "change_type": change_type,
                "qualified_name": qual,
                "symbol_kind": kind,
                "exported": exported,
                "breaking_candidate": breaking,
            }
            rows.append(row)
            if breaking:
                project_breaking[project].append({
                    "sha": change.get("sha"),
                    "short_sha": change.get("short_sha"),
                    "date": change.get("date"),
                    "path": path,
                    "change_type": change_type,
                    "qualified_name": qual,
                    "symbol_kind": kind,
                })

    project_summaries: list[dict[str, Any]] = []
    for project in sorted({*project_breaking, *project_touches, *project_commit_count}):
        project_summaries.append({
            "project": project,
            "commits_touched": len(project_commit_count.get(project, set()) - {""}),
            "symbol_touches_by_kind": dict(project_touches.get(project, {})),
            "breaking_candidate_count": len(project_breaking.get(project, [])),
            "breaking_candidates": project_breaking.get(project, [])[:25],
        })

    caveats = ["intersection is path-level only; line-range diffing is not run per commit"]
    if not symbols_by_path:
        caveats.append("active_symbol_index payload unavailable or empty (tree-sitter grammars missing?)")
    if not file_changes:
        caveats.append("active_file_change_facts payload unavailable or empty for selected window")

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "join": "symbol_index × file_change_facts on (project, path)",
            "breaking_candidate": "exported symbol whose containing file was deleted or renamed",
            "scope": "path-level only — line-range hunks are not consulted",
        },
        "projects": project_summaries,
        "events": rows,
        "caveats": caveats,
    }


def run_active_symbol_changes(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    symbol_index_file: str | PathLike[str] | None = None,
    file_changes_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_symbol_changes(
        start=start, end=end, projects=projects,
        symbol_index_file=symbol_index_file, file_changes_file=file_changes_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _index_symbols_by_path(
    payload: dict[str, Any],
    selected: set[str],
) -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return {}
    for row in projects:
        if not isinstance(row, dict):
            continue
        project = row.get("project")
        if not project or (selected and project not in selected):
            continue
        for sym in row.get("symbols") or []:
            if not isinstance(sym, dict):
                continue
            path = sym.get("path")
            if not path:
                continue
            out[(project, path)].append(sym)
    return {key: tuple(value) for key, value in out.items()}


def _filter_changes(payload: dict[str, Any], selected: set[str]) -> list[dict[str, Any]]:
    rows = payload.get("file_changes")
    if not isinstance(rows, list):
        return []
    return [
        row for row in rows
        if isinstance(row, dict) and (not selected or row.get("project") in selected)
    ]


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
