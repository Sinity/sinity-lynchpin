"""Shared active-project git fact extraction."""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from pathlib import Path
from typing import Any

from ...core.projects import ALL_PROJECTS, ProjectProfile, canonical_project_name, project_profiles
from ...sources.github import extract_commit_refs
from ..core.io import resolve_analysis_path, save_json

# Use separators that are not treated as line boundaries by str.splitlines().
_COMMIT_PREFIX = "\x02"
_FIELD_SEP = "\x1f"
_CONVENTIONAL_RE = re.compile(r"^([a-z]+)(?:\(([^)]+)\))?(!)?:\s*(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class ConventionalSubject:
    kind: str
    scope: str | None
    description: str
    breaking: bool

    @property
    def signature(self) -> str:
        return f"{self.kind}({self.scope})" if self.scope else self.kind

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "scope": self.scope,
            "signature": self.signature,
            "description": self.description,
            "breaking": self.breaking,
        }


@dataclass(frozen=True)
class ActivePathChange:
    status_code: str
    change_type: str
    path: str
    previous_path: str | None
    category: str | None
    path_root: str
    lines_added: int = 0
    lines_deleted: int = 0

    @property
    def is_classified(self) -> bool:
        return self.category is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "previous_path": self.previous_path,
            "status_code": self.status_code,
            "change_type": self.change_type,
            "category": self.category,
            "path_root": self.path_root,
            "classified": self.is_classified,
            "lines_added": self.lines_added,
            "lines_deleted": self.lines_deleted,
            "lines_changed": self.lines_added + self.lines_deleted,
        }


@dataclass
class ActiveCommitRecord:
    project: str
    repo_path: Path
    default_branch: str
    head: str | None
    sha: str
    short_sha: str
    author: str
    timestamp: str
    subject: str
    parent_count: int
    file_changes: list[ActivePathChange] = field(default_factory=list)

    @property
    def day(self) -> str:
        return self.timestamp[:10] or "unknown"

    @property
    def conventional(self) -> ConventionalSubject:
        return conventional_subject(self.subject)

    @property
    def paths(self) -> set[str]:
        return {change.path for change in self.file_changes}

    @property
    def classified_paths(self) -> set[str]:
        return {change.path for change in self.file_changes if change.is_classified}

    @property
    def categories(self) -> Counter[str]:
        return Counter(change.category for change in self.file_changes if change.category is not None)

    @property
    def path_roots(self) -> Counter[str]:
        return Counter(change.path_root for change in self.file_changes if change.path_root)

    @property
    def change_types(self) -> Counter[str]:
        return Counter(change.change_type for change in self.file_changes)

    @property
    def lines_added(self) -> int:
        return sum(change.lines_added for change in self.file_changes)

    @property
    def lines_deleted(self) -> int:
        return sum(change.lines_deleted for change in self.file_changes)

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_deleted

    @property
    def github_refs(self) -> dict[str, list[int]]:
        refs = extract_commit_refs(self.subject)
        return {"prs": sorted(refs["prs"]), "issues": sorted(refs["issues"])}

    def to_summary_json(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "short_sha": self.short_sha,
            "author": self.author,
            "timestamp": self.timestamp,
            "date": self.day,
            "subject": self.subject,
            "parent_count": self.parent_count,
            "files_changed": len(self.paths),
            "lines_added": self.lines_added,
            "lines_deleted": self.lines_deleted,
            "lines_changed": self.lines_changed,
            "classified_files_changed": len(self.classified_paths),
            "top_paths": sorted(self.classified_paths or self.paths)[:12],
            "categories": dict(sorted(self.categories.items())),
            "change_types": dict(sorted(self.change_types.items())),
            "conventional": self.conventional.to_json(),
            "github_refs": self.github_refs,
        }

    def to_commit_fact_json(self) -> dict[str, Any]:
        categories = self.categories
        path_roots = self.path_roots
        conventional = self.conventional
        return {
            "project": self.project,
            "sha": self.sha,
            "short_sha": self.short_sha,
            "author": self.author,
            "timestamp": self.timestamp,
            "date": self.day,
            "subject": self.subject,
            "parent_count": self.parent_count,
            "default_branch": self.default_branch,
            "head": self.head,
            "conventional_kind": conventional.kind,
            "conventional_scope": conventional.scope,
            "conventional_signature": conventional.signature,
            "conventional_description": conventional.description,
            "breaking_change": conventional.breaking,
            "github_refs": self.github_refs,
            "files_changed": len(self.paths),
            "lines_added": self.lines_added,
            "lines_deleted": self.lines_deleted,
            "lines_changed": self.lines_changed,
            "classified_files_changed": len(self.classified_paths),
            "categories": dict(categories.most_common()),
            "path_roots": dict(path_roots.most_common()),
            "change_types": dict(self.change_types.most_common()),
            "paths": sorted(self.paths),
        }


@dataclass(frozen=True)
class ActiveProjectGitFacts:
    project: str
    path: Path
    exists: bool
    is_git_repo: bool
    default_branch: str | None
    head: str | None
    commits: tuple[ActiveCommitRecord, ...]

    @property
    def active_days(self) -> set[str]:
        return {commit.day for commit in self.commits if commit.day != "unknown"}

    @property
    def file_change_count(self) -> int:
        return sum(len(commit.file_changes) for commit in self.commits)

    @property
    def classified_file_change_count(self) -> int:
        return sum(len(commit.classified_paths) for commit in self.commits)

    def to_project_json(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "path": str(self.path),
            "exists": self.exists,
            "is_git_repo": self.is_git_repo,
            "default_branch": self.default_branch,
            "head": self.head,
            "commit_count": len(self.commits),
            "active_days": len(self.active_days),
            "file_change_count": self.file_change_count,
            "classified_file_change_count": self.classified_file_change_count,
            "status": "available" if self.exists and self.is_git_repo else "missing",
        }


def build_active_commit_facts(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    profiles: Mapping[str, ProjectProfile] | None = None,
    project_facts: Sequence[ActiveProjectGitFacts] | None = None,
) -> dict[str, Any]:
    """Build compact per-commit facts for active projects."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))
    facts = tuple(project_facts or collect_active_git_facts(start=start, end=end, projects=projects, profiles=profiles))
    commits = [commit.to_commit_fact_json() for project in facts for commit in project.commits]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": _window(start, end),
        "methodology": _methodology(),
        "projects": [project.to_project_json() for project in facts],
        "commits": commits,
        "summary": _summary(facts),
    }


def build_active_file_change_facts(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    profiles: Mapping[str, ProjectProfile] | None = None,
    project_facts: Sequence[ActiveProjectGitFacts] | None = None,
) -> dict[str, Any]:
    """Build per-file path-status facts for active-project commits."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))
    facts = tuple(project_facts or collect_active_git_facts(start=start, end=end, projects=projects, profiles=profiles))
    rows = [
        _file_change_fact_json(commit, change)
        for project in facts
        for commit in project.commits
        for change in commit.file_changes
    ]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": _window(start, end),
        "methodology": _methodology(),
        "projects": [project.to_project_json() for project in facts],
        "file_changes": rows,
        "summary": _summary(facts),
    }


def run_active_git_facts(
    commit_out_file: str | PathLike[str],
    file_change_out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize active commit and file-change fact artifacts from one scan."""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))
    facts = collect_active_git_facts(start=start, end=end, projects=projects)
    commit_payload = build_active_commit_facts(start=start, end=end, project_facts=facts)
    file_payload = build_active_file_change_facts(start=start, end=end, project_facts=facts)
    save_json(resolve_analysis_path(commit_out_file), commit_payload, sort_keys=True)
    save_json(resolve_analysis_path(file_change_out_file), file_payload, sort_keys=True)
    return commit_payload, file_payload


def collect_active_git_facts(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    profiles: Mapping[str, ProjectProfile] | None = None,
) -> tuple[ActiveProjectGitFacts, ...]:
    selected = select_active_profiles(projects=projects, profiles=profiles)
    return tuple(
        _project_git_facts(name, profile, start=start, end=end)
        for name, profile in sorted(selected.items())
    )


def select_active_profiles(
    *,
    projects: Sequence[str] | None,
    profiles: Mapping[str, ProjectProfile] | None,
) -> dict[str, ProjectProfile]:
    available = dict(profiles or project_profiles())
    if projects:
        selected: dict[str, ProjectProfile] = {}
        missing: list[str] = []
        for raw in projects:
            project = canonical_project_name(raw, include_inactive=True) or str(raw)
            profile = available.get(project)
            if profile is None:
                missing.append(str(raw))
                continue
            selected[project] = profile
        if missing:
            raise ValueError(f"Unknown project(s): {', '.join(sorted(missing))}")
        return selected
    if profiles is not None:
        return available
    return {
        name: profile
        for name, profile in available.items()
        if (entry := ALL_PROJECTS.get(name)) is not None and entry.active
    }


def git_output(path: Path, args: Sequence[str]) -> str | None:
    if not path.exists():
        return None
    proc = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def default_branch(path: Path) -> str | None:
    remote_head = git_output(path, ("symbolic-ref", "--short", "refs/remotes/origin/HEAD"))
    if remote_head and "/" in remote_head:
        return remote_head.split("/", 1)[1]
    for candidate in ("master", "main"):
        if git_output(path, ("rev-parse", "--verify", candidate)):
            return candidate
    return git_output(path, ("branch", "--show-current")) or "HEAD"


def tracked_files(path: Path) -> tuple[str, ...]:
    output = git_output(path, ("ls-files", "-z"))
    if not output:
        return ()
    return tuple(item for item in output.split("\0") if item)


def recent_active_commits(
    *,
    project: str,
    path: Path,
    profile: ProjectProfile,
    ref: str,
    start: date,
    end: date,
    head: str | None = None,
) -> tuple[ActiveCommitRecord, ...]:
    before = end + timedelta(days=1)
    numstat = _numstat_by_commit_path(path=path, ref=ref, start=start, before=before)
    cmd = [
        "git",
        "log",
        ref,
        "--first-parent",
        "--reverse",
        "--date=iso-strict",
        f"--after={start.isoformat()}",
        f"--before={before.isoformat()}",
        f"--pretty=format:{_COMMIT_PREFIX}%H{_FIELD_SEP}%h{_FIELD_SEP}%aN{_FIELD_SEP}%aI{_FIELD_SEP}%s{_FIELD_SEP}%P",
        "--name-status",
    ]
    proc = subprocess.run(cmd, cwd=path, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ()

    records: list[ActiveCommitRecord] = []
    current: ActiveCommitRecord | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and _commit_in_window(current, start=start, end=end):
            records.append(current)
        current = None

    for line in proc.stdout.splitlines():
        if not line:
            continue
        if line.startswith(_COMMIT_PREFIX):
            flush()
            fields = line.removeprefix(_COMMIT_PREFIX).split(_FIELD_SEP, 5)
            if len(fields) < 5:
                continue
            parents = fields[5].split() if len(fields) > 5 and fields[5].strip() else []
            current = ActiveCommitRecord(
                project=project,
                repo_path=path,
                default_branch=ref,
                head=head,
                sha=fields[0],
                short_sha=fields[1],
                author=fields[2],
                timestamp=fields[3],
                subject=fields[4],
                parent_count=len(parents),
            )
            continue
        if current is None:
            continue
        change = _parse_name_status(line, profile)
        if change is not None:
            added, deleted = numstat.get((current.sha, change.path), (0, 0))
            current.file_changes.append(_with_churn(change, lines_added=added, lines_deleted=deleted))
    flush()
    return tuple(records)


def conventional_subject(subject: str) -> ConventionalSubject:
    match = _CONVENTIONAL_RE.match(subject or "")
    if not match:
        return ConventionalSubject(kind="other", scope=None, description=(subject or "").strip(), breaking=False)
    scope = (match.group(2) or "").strip().lower() or None
    return ConventionalSubject(
        kind=match.group(1).lower(),
        scope=scope,
        description=(match.group(4) or "").strip(),
        breaking=bool(match.group(3)),
    )


def conventional_kind(subject: str) -> str:
    return conventional_subject(subject).kind


def _commit_in_window(commit: ActiveCommitRecord, *, start: date, end: date) -> bool:
    try:
        day = date.fromisoformat(commit.day)
    except ValueError:
        return False
    return start <= day <= end


def path_root(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    if not parts:
        return "unknown"
    if parts[0] in {"src", "lynchpin", "tests", "test", "unit", "integration"} and len(parts) >= 2:
        return parts[1]
    return parts[0]


def _project_git_facts(
    name: str,
    profile: ProjectProfile,
    *,
    start: date,
    end: date,
) -> ActiveProjectGitFacts:
    path = profile.path
    exists = path.exists()
    is_git = (path / ".git").exists()
    branch = default_branch(path) if is_git else None
    head = git_output(path, ("rev-parse", "--short", "HEAD")) if is_git else None
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
    return ActiveProjectGitFacts(
        project=name,
        path=path,
        exists=exists,
        is_git_repo=is_git,
        default_branch=branch,
        head=head,
        commits=commits,
    )


def _parse_name_status(line: str, profile: ProjectProfile) -> ActivePathChange | None:
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    status_code = parts[0].strip()
    if not status_code:
        return None
    previous_path: str | None = None
    if status_code.startswith(("R", "C")) and len(parts) >= 3:
        previous_path = parts[1].strip() or None
        path = parts[2].strip()
    else:
        path = parts[1].strip()
    if not path:
        return None
    category = profile.classify(path)
    if category is None and previous_path is not None:
        category = profile.classify(previous_path)
    return ActivePathChange(
        status_code=status_code,
        change_type=_change_type(status_code),
        path=path,
        previous_path=previous_path,
        category=category,
        path_root=path_root(path),
    )


def _with_churn(change: ActivePathChange, *, lines_added: int, lines_deleted: int) -> ActivePathChange:
    return ActivePathChange(
        status_code=change.status_code,
        change_type=change.change_type,
        path=change.path,
        previous_path=change.previous_path,
        category=change.category,
        path_root=change.path_root,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
    )


def _numstat_by_commit_path(
    *,
    path: Path,
    ref: str,
    start: date,
    before: date,
) -> dict[tuple[str, str], tuple[int, int]]:
    cmd = [
        "git",
        "log",
        ref,
        "--first-parent",
        "--reverse",
        "--date=iso-strict",
        f"--after={start.isoformat()}",
        f"--before={before.isoformat()}",
        f"--pretty=format:{_COMMIT_PREFIX}%H",
        "--numstat",
    ]
    proc = subprocess.run(cmd, cwd=path, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return {}
    out: dict[tuple[str, str], tuple[int, int]] = {}
    sha: str | None = None
    for line in proc.stdout.splitlines():
        if not line:
            continue
        if line.startswith(_COMMIT_PREFIX):
            sha = line.removeprefix(_COMMIT_PREFIX).strip()
            continue
        if sha is None:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added = _parse_numstat_count(parts[0])
        deleted = _parse_numstat_count(parts[1])
        rel = _normalize_numstat_path(parts[2])
        if rel:
            out[(sha, rel)] = (added, deleted)
    return out


def _parse_numstat_count(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _normalize_numstat_path(value: str) -> str:
    value = value.strip()
    if " => " not in value:
        return value
    # Git uses forms such as ``src/{old.py => new.py}`` or
    # ``{old/path.py => new/path.py}``; keep the post-rename path so it
    # matches ``git log --name-status``.
    prefix, _, suffix = value.partition(" => ")
    if "{" in prefix:
        prefix = prefix.rsplit("{", 1)[0]
    if "}" in suffix:
        suffix = suffix.split("}", 1)[0] + suffix.split("}", 1)[1]
    return f"{prefix}{suffix}".strip()


def _change_type(status_code: str) -> str:
    code = status_code[:1].upper()
    return {
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type_changed",
        "U": "unmerged",
        "X": "unknown",
        "B": "pairing_broken",
    }.get(code, "unknown")


def _file_change_fact_json(commit: ActiveCommitRecord, change: ActivePathChange) -> dict[str, Any]:
    return {
        "project": commit.project,
        "sha": commit.sha,
        "short_sha": commit.short_sha,
        "timestamp": commit.timestamp,
        "date": commit.day,
        "subject": commit.subject,
        "default_branch": commit.default_branch,
        "path": change.path,
        "previous_path": change.previous_path,
        "path_root": change.path_root,
        "category": change.category,
        "classified": change.is_classified,
        "status_code": change.status_code,
        "change_type": change.change_type,
        "lines_added": change.lines_added,
        "lines_deleted": change.lines_deleted,
        "lines_changed": change.lines_added + change.lines_deleted,
        "conventional_kind": commit.conventional.kind,
        "conventional_scope": commit.conventional.scope,
        "conventional_signature": commit.conventional.signature,
        "github_refs": commit.github_refs,
    }


def _window(start: date, end: date) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "git_history": "default-branch first-parent commits",
        "file_detail": "git name-status path facts joined with git numstat churn",
        "line_churn": "materialized from git log --numstat; binary file churn is recorded as 0",
    }


def _methodology() -> dict[str, Any]:
    return {
        "project_scope": "active project registry unless projects are explicitly selected",
        "commit_scope": "bounded git log over the inferred default branch with --first-parent",
        "file_facts": "git name-status rows; renames preserve previous_path and current path",
        "taxonomy": "project registry classifier supplies category; unclassified rows remain visible with classified=false",
        "velocity_caveat": "commit and file counts are heartbeat/surface signals only; scope, lifecycle, AI support, and cross-source evidence remain separate dimensions",
        "churn_caveat": "line additions/deletions are intentionally absent from this fast current-state substrate to avoid huge numstat costs and misleading move/delete spikes",
    }


def _summary(facts: Sequence[ActiveProjectGitFacts]) -> dict[str, Any]:
    available = [project for project in facts if project.exists and project.is_git_repo]
    return {
        "project_count": len(facts),
        "available_project_count": len(available),
        "commit_count": sum(len(project.commits) for project in facts),
        "file_change_count": sum(project.file_change_count for project in facts),
        "classified_file_change_count": sum(project.classified_file_change_count for project in facts),
        "active_days": len({day for project in facts for day in project.active_days}),
    }


__all__ = [
    "ActiveCommitRecord",
    "ActivePathChange",
    "ActiveProjectGitFacts",
    "ConventionalSubject",
    "build_active_commit_facts",
    "build_active_file_change_facts",
    "collect_active_git_facts",
    "conventional_kind",
    "conventional_subject",
    "default_branch",
    "git_output",
    "path_root",
    "recent_active_commits",
    "run_active_git_facts",
    "select_active_profiles",
    "tracked_files",
]
