"""Active-project code and git snapshot materializer."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ...core.projects import ALL_PROJECTS, ProjectProfile
from lynchpin.core.io import resolve_analysis_path, save_json
from .git_facts import (
    ActiveCommitRecord,
    conventional_kind,
    default_branch,
    git_output,
    recent_active_commits,
    select_active_profiles,
    tracked_files,
)

_MAX_TEXT_BYTES = 2_000_000


def build_active_project_snapshot(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    profiles: Mapping[str, ProjectProfile] | None = None,
) -> dict[str, Any]:
    """Build deterministic active-project code and first-parent git facts."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))
    selected = select_active_profiles(projects=projects, profiles=profiles)
    rows = [
        _project_row(name, profile, start=start, end=end)
        for name, profile in sorted(selected.items())
    ]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "git_history": "default-branch first-parent commits",
        },
        "methodology": {
            "project_scope": "active project registry unless projects are explicitly selected",
            "structure": "tracked files in the current checkout, classified by project registry file classifier",
            "recent_git": "git log --name-status over the inferred default branch with --first-parent and a bounded date window",
            "velocity_caveat": "commit counts are a heartbeat signal only; scope, touched surfaces, quality gates, and cross-source evidence remain separate dimensions",
            "touch_caveat": "raw path-touch counts can be dominated by large moves/deletes; capped_category_touches limits each commit/category contribution to 25 for a robust companion view",
        },
        "projects": rows,
    }


def run_active_project_snapshot(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Materialize the active-project snapshot artifact."""
    payload = build_active_project_snapshot(start=start, end=end, projects=projects)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _project_row(name: str, profile: ProjectProfile, *, start: date, end: date) -> dict[str, Any]:
    path = profile.path
    exists = path.exists()
    is_git = (path / ".git").exists()
    branch = default_branch(path) if is_git else None
    head = git_output(path, ("rev-parse", "--short", "HEAD")) if is_git else None
    dirty = bool(git_output(path, ("status", "--porcelain"))) if is_git else False
    tracked = tracked_files(path) if is_git else ()
    commits = (
        recent_active_commits(
            project=name,
            path=path,
            profile=profile,
            ref=branch or "HEAD",
            start=start,
            end=end,
            head=head,
        )
        if is_git
        else ()
    )
    return {
        "project": name,
        "path": str(path),
        "exists": exists,
        "is_git_repo": is_git,
        "default_branch": branch,
        "head": head,
        "dirty": dirty,
        "active_registry_entry": bool((entry := ALL_PROJECTS.get(name)) and entry.active),
        "structure": _structure_summary(path, profile, tracked),
        "quality_gates": _quality_gates(path, tracked),
        "recent_git": _recent_summary(commits),
    }


def _structure_summary(path: Path, profile: ProjectProfile, tracked_files: Sequence[str]) -> dict[str, Any]:
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    extensions: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    skipped_files = 0
    skipped_bytes = 0
    counted_files = 0
    counted_lines = 0
    for rel in tracked_files:
        category = profile.classify(rel)
        if category is None:
            continue
        file_path = path / rel
        try:
            size = file_path.stat().st_size
        except OSError:
            skipped_files += 1
            continue
        if size > _MAX_TEXT_BYTES:
            skipped_files += 1
            skipped_bytes += size
            continue
        lines = _line_count(file_path)
        if lines is None:
            skipped_files += 1
            skipped_bytes += size
            continue
        counted_files += 1
        counted_lines += lines
        categories[category]["files"] += 1
        categories[category]["lines"] += lines
        ext = Path(rel).suffix.lower() or "(none)"
        extensions[ext]["files"] += 1
        extensions[ext]["lines"] += lines
    return {
        "tracked_files": len(tracked_files),
        "counted_files": counted_files,
        "counted_lines": counted_lines,
        "skipped_files": skipped_files,
        "skipped_bytes": skipped_bytes,
        "categories": dict(sorted(categories.items())),
        "extensions": dict(sorted(extensions.items(), key=lambda item: (-item[1]["lines"], item[0]))[:12]),
    }


def _line_count(path: Path) -> int | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    if not data:
        return 0
    return len(data.splitlines())


def _quality_gates(path: Path, tracked_files: Sequence[str]) -> tuple[str, ...]:
    tracked = set(tracked_files)
    gates: set[str] = set()
    if "pyproject.toml" in tracked:
        gates.add("pyproject")
        try:
            text = (path / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for token, gate in (("ruff", "ruff"), ("mypy", "mypy"), ("pytest", "pytest")):
            if token in text:
                gates.add(gate)
    if "Cargo.toml" in tracked:
        gates.add("cargo")
    if "flake.nix" in tracked:
        gates.add("nix_flake")
    if "justfile" in tracked or "Justfile" in tracked:
        gates.add("just")
    if any(rel.startswith(".github/workflows/") for rel in tracked):
        gates.add("github_actions")
    if any(rel.startswith(("tests/", "test/", "unit/", "integration/")) for rel in tracked):
        gates.add("tests")
    return tuple(sorted(gates))


def _recent_summary(commits: Sequence[ActiveCommitRecord]) -> dict[str, Any]:
    active_days = {commit.day for commit in commits if commit.day != "unknown"}
    category_churn: Counter[str] = Counter()
    capped_category_touches: Counter[str] = Counter()
    conventional_kinds: Counter[str] = Counter()
    files_changed = 0
    for commit in commits:
        category_churn.update(commit.categories)
        capped_category_touches.update(
            {category: min(count, 25) for category, count in commit.categories.items()}
        )
        conventional_kinds[conventional_kind(commit.subject)] += 1
        files_changed += len(commit.classified_paths)
    newest = sorted(commits, key=lambda item: item.timestamp, reverse=True)
    largest = sorted(commits, key=lambda item: len(item.classified_paths), reverse=True)
    return {
        "commit_count": len(commits),
        "active_days": len(active_days),
        "merge_commit_count": sum(1 for commit in commits if commit.parent_count > 1),
        "files_changed": files_changed,
        "category_touches": dict(category_churn.most_common()),
        "capped_category_touches": dict(capped_category_touches.most_common()),
        "conventional_kinds": dict(conventional_kinds.most_common()),
        "top_subjects": [commit.subject for commit in newest[:8]],
        "large_touch_commits": [
            {
                "short_sha": commit.short_sha,
                "date": commit.day,
                "subject": commit.subject,
                "files_changed": len(commit.classified_paths),
            }
            for commit in largest[:8]
            if len(commit.classified_paths) >= 100
        ],
        "sample_commits": [commit.to_summary_json() for commit in newest[:40]],
    }


__all__ = ["build_active_project_snapshot", "run_active_project_snapshot"]
