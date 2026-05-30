"""Line-range symbol diff intersection.

Graduates ``symbol_changes`` (path-level) to real diff-hunk intersection.
For every commit in ``active_commit_facts.json``, runs
``git show <sha> --unified=0 --no-color`` once, parses hunk headers, and
intersects new-side line ranges with symbol line ranges from
``active_symbol_index.json``.

Emits per (commit, path, symbol): ``lines_added``, ``lines_removed``,
plus the breaking-candidate flag for exported symbols whose containing
file was deleted or renamed.

Returns empty results with a caveat when the symbol index product is
valid but empty (tree-sitter grammars missing). Skips the commit if it has no
indexed symbols in any of its changed paths.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ..core.io import load_json_object, resolve_analysis_path, save_json


_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
)
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_GIT_TIMEOUT_S = 60
# No commit-count cap — the caller controls cardinality via the
# ``start``/``end`` window.  A narrow window (e.g. 31 days → a few
# hundred commits) keeps ``git show`` cost bounded; widening the
# window intentionally means accepting the extra work.


def build_active_symbol_diffs(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    commit_facts_file: str | PathLike[str] | None = None,
    symbol_index_file: str | PathLike[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    commit_payload = load_json_object(
        commit_facts_file or resolve_analysis_path("active_commit_facts.json"),
        label="active commit facts",
    )
    index_payload = load_json_object(
        symbol_index_file or resolve_analysis_path("active_symbol_index.json"),
        label="active symbol index",
    )
    snapshot_payload = load_json_object(
        snapshot_file or resolve_analysis_path("active_project_snapshot.json"),
        label="active project snapshot",
    )

    selected = set(projects or ())
    project_paths = _project_paths(snapshot_payload, selected)
    symbols_by_path = _index_symbols_by_path(index_payload, selected)

    caveats: list[str] = []
    if not symbols_by_path:
        caveats.append(
            "active_symbol_index has no usable symbols (likely tree-sitter grammars missing); "
            "no diff intersection possible"
        )
        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "methodology": _methodology(),
            "projects": [],
            "events": [],
            "caveats": caveats,
        }

    commits_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for commit in commit_payload.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        project = commit.get("project")
        if not project or (selected and project not in selected):
            continue
        commits_by_project[project].append(commit)

    project_summaries: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for project, commits in sorted(commits_by_project.items()):
        repo_root = project_paths.get(project)
        if not repo_root or not Path(repo_root).is_dir():
            caveats.append(f"{project}: repository root not found on disk; skipped")
            continue
        # process most recent commits first
        commits.sort(key=lambda c: c.get("timestamp") or "", reverse=True)
        project_events, project_summary = _process_project(
            project=project,
            repo_root=Path(repo_root),
            commits=commits,
            symbols_by_path=symbols_by_path,
        )
        events.extend(project_events)
        project_summaries.append(project_summary)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": _methodology(),
        "projects": project_summaries,
        "events": events,
        "caveats": caveats,
    }


def run_active_symbol_diffs(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    commit_facts_file: str | PathLike[str] | None = None,
    symbol_index_file: str | PathLike[str] | None = None,
    snapshot_file: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_symbol_diffs(
        start=start, end=end, projects=projects,
        commit_facts_file=commit_facts_file,
        symbol_index_file=symbol_index_file,
        snapshot_file=snapshot_file,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _process_project(
    *,
    project: str,
    repo_root: Path,
    commits: list[dict[str, Any]],
    symbols_by_path: dict[tuple[str, str], tuple[dict[str, Any], ...]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    indexed_paths_for_project = {
        path for (proj, path) in symbols_by_path if proj == project
    }
    if not indexed_paths_for_project:
        return [], {
            "project": project,
            "commit_count": 0,
            "indexed_path_count": 0,
            "events_emitted": 0,
            "breaking_candidate_count": 0,
            "top_touched_symbols": [],
            "breaking_candidates": [],
        }

    events: list[dict[str, Any]] = []
    touch_counter: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {
        "qualified_name": "",
        "symbol_kind": "",
        "exported": False,
        "path": "",
        "touch_count": 0,
        "lines_added": 0,
        "lines_removed": 0,
    })
    breaking: list[dict[str, Any]] = []

    for commit in commits:
        sha = commit.get("sha")
        if not sha:
            continue
        commit_paths = set(commit.get("paths") or [])
        relevant = commit_paths & indexed_paths_for_project
        if not relevant:
            continue
        diff_blocks = _git_show_unified0(repo_root=repo_root, sha=sha)
        if diff_blocks is None:
            continue
        for path, hunks in diff_blocks.items():
            if path not in relevant:
                continue
            symbols = symbols_by_path.get((project, path), ())
            change_type = _change_type_for(commit, path)
            for sym in symbols:
                start_line = int(sym.get("start_line") or 0)
                end_line = int(sym.get("end_line") or 0)
                added = removed = 0
                for h in hunks:
                    added += _overlap(h["new_start"], h["new_count"], start_line, end_line)
                    removed += _overlap(h["old_start"], h["old_count"], start_line, end_line)
                if added == 0 and removed == 0:
                    continue
                qual = sym.get("qualified_name") or ""
                kind = sym.get("symbol_kind") or "unknown"
                exported = bool(sym.get("exported"))
                event = {
                    "project": project,
                    "sha": sha,
                    "short_sha": commit.get("short_sha"),
                    "date": commit.get("date"),
                    "path": path,
                    "qualified_name": qual,
                    "symbol_kind": kind,
                    "exported": exported,
                    "change_type": change_type,
                    "lines_added": added,
                    "lines_removed": removed,
                    "breaking_candidate": exported and change_type[:1] in {"D", "R"},
                }
                events.append(event)
                key = f"{path}::{qual}"
                bucket = touch_counter[key]
                bucket["qualified_name"] = qual
                bucket["symbol_kind"] = kind
                bucket["exported"] = exported
                bucket["path"] = path
                bucket["touch_count"] += 1
                bucket["lines_added"] += added
                bucket["lines_removed"] += removed
                if event["breaking_candidate"]:
                    breaking.append({
                        "sha": sha,
                        "short_sha": commit.get("short_sha"),
                        "path": path,
                        "qualified_name": qual,
                        "symbol_kind": kind,
                        "change_type": change_type,
                    })

    top = sorted(
        touch_counter.values(),
        key=lambda r: (r["touch_count"], r["lines_added"] + r["lines_removed"]),
        reverse=True,
    )[:25]
    summary = {
        "project": project,
        "commit_count": len(commits),
        "indexed_path_count": len(indexed_paths_for_project),
        "events_emitted": len(events),
        "breaking_candidate_count": len(breaking),
        "top_touched_symbols": top,
        "breaking_candidates": breaking[:25],
    }
    return events, summary


def _git_show_unified0(*, repo_root: Path, sha: str) -> dict[str, list[dict[str, int]]] | None:
    cmd = [
        "git", "show", sha,
        "--unified=0", "--no-color", "--no-renames",
        "--pretty=format:",  # no commit header
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(repo_root),
            capture_output=True, timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    # Git output may contain non-UTF-8 bytes from old commits (binary
    # files, legacy encodings). Replace invalid sequences rather than
    # crashing the whole symbol-extraction step.
    stdout = result.stdout.decode("utf-8", errors="replace")
    return _parse_unified0_diff(stdout)


def _parse_unified0_diff(output: str) -> dict[str, list[dict[str, int]]]:
    out: dict[str, list[dict[str, int]]] = defaultdict(list)
    current_path: str | None = None
    for line in output.splitlines():
        header_match = _DIFF_HEADER_RE.match(line)
        if header_match:
            current_path = header_match.group(2)
            continue
        if current_path is None:
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match:
            old_start, old_count, new_start, new_count = hunk_match.groups()
            out[current_path].append({
                "old_start": int(old_start),
                "old_count": int(old_count) if old_count is not None else 1,
                "new_start": int(new_start),
                "new_count": int(new_count) if new_count is not None else 1,
            })
    return dict(out)


def _overlap(hunk_start: int, hunk_count: int, sym_start: int, sym_end: int) -> int:
    if hunk_count == 0 or sym_end < sym_start:
        return 0
    hunk_end = hunk_start + hunk_count - 1
    overlap_start = max(hunk_start, sym_start)
    overlap_end = min(hunk_end, sym_end)
    return max(0, overlap_end - overlap_start + 1)


def _change_type_for(commit: dict[str, Any], path: str) -> str:
    change_types = commit.get("change_types") or {}
    if isinstance(change_types, dict) and change_types:
        # commit-level summary; unable to attribute per-path, but pick the
        # most-prevalent code as a hint
        for code in ("D", "R", "A", "M", "T", "C"):
            if change_types.get(code):
                return code
    return "M"


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


def _project_paths(snapshot: dict[str, Any], selected: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = snapshot.get("projects")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project") or "")
        if not project or (selected and project not in selected):
            continue
        path = str(row.get("path") or "")
        if path:
            out[project] = path
    return out


def _methodology() -> dict[str, Any]:
    return {
        "scope": "git show <sha> --unified=0 per commit; hunk new-side ranges intersected with symbol [start_line, end_line]",
        "lines_added": "count of lines whose new-side number falls inside the symbol range",
        "lines_removed": "count of lines whose old-side number falls inside the symbol range",
        "breaking_candidate": "exported symbol whose change_type begins with D (delete) or R (rename)",
    }


__all__ = ["build_active_symbol_diffs", "run_active_symbol_diffs"]
