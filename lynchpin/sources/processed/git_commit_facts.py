"""Commit-level git evidence with changed paths and optional patch excerpts."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Sequence

from ..indices.gitstats import active_repo_paths


@dataclass(frozen=True)
class GitCommitFact:
    repo: str
    commit: str
    authored_at: datetime
    author: str
    subject: str
    lines_added: int
    lines_deleted: int
    lines_changed: int
    files_changed: int
    paths: tuple[str, ...]
    path_roots: tuple[str, ...]

    @property
    def date(self) -> date:
        return self.authored_at.date()


@dataclass(frozen=True)
class GitFileChangeFact:
    repo: str
    commit: str
    authored_at: datetime
    path: str
    path_root: str
    lines_added: int
    lines_deleted: int
    lines_changed: int

    @property
    def date(self) -> date:
        return self.authored_at.date()


@dataclass(frozen=True)
class GitPatchExcerpt:
    line_count: int
    truncated: bool
    patch_excerpt: str


def iter_git_commit_facts(
    *,
    start: date,
    end: date,
    repos: Sequence[Path] | None = None,
) -> Iterator[GitCommitFact]:
    repo_paths = list(repos) if repos is not None else active_repo_paths()
    for repo_path in sorted(repo_paths, key=lambda path: path.name):
        for record in _iter_repo_commit_records(repo_path, start=start, end=end):
            yield _commit_fact_from_record(record)


def iter_git_file_change_facts(
    *,
    start: date,
    end: date,
    repos: Sequence[Path] | None = None,
) -> Iterator[GitFileChangeFact]:
    repo_paths = list(repos) if repos is not None else active_repo_paths()
    for repo_path in sorted(repo_paths, key=lambda path: path.name):
        for record in _iter_repo_commit_records(repo_path, start=start, end=end):
            authored_at = record.authored_at
            for path, lines_added, lines_deleted in record.path_changes:
                yield GitFileChangeFact(
                    repo=record.repo,
                    commit=record.commit,
                    authored_at=authored_at,
                    path=path,
                    path_root=_path_root(path),
                    lines_added=lines_added,
                    lines_deleted=lines_deleted,
                    lines_changed=lines_added + lines_deleted,
                )


def git_patch_excerpt(
    *,
    repo_path: Path,
    commit: str,
    max_lines: int = 120,
) -> GitPatchExcerpt:
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "show",
        "--no-color",
        "--format=",
        "--unified=3",
        commit,
    ]
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except Exception:
        return GitPatchExcerpt(line_count=0, truncated=False, patch_excerpt="")

    output = raw.decode("utf-8", errors="replace")
    lines = output.splitlines()
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    return GitPatchExcerpt(
        line_count=len(output.splitlines()),
        truncated=truncated,
        patch_excerpt="\n".join(lines),
    )


@dataclass(frozen=True)
class _RepoCommitRecord:
    repo: str
    commit: str
    authored_at: datetime
    author: str
    subject: str
    path_changes: tuple[tuple[str, int, int], ...]


def _iter_repo_commit_records(
    repo_path: Path,
    *,
    start: date,
    end: date,
) -> Iterator[_RepoCommitRecord]:
    if not (repo_path / ".git").is_dir():
        return

    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--all",
        "--date=iso-strict",
        "--pretty=format:COMMIT|%H|%aI|%aN|%s",
        "--numstat",
        f"--after={(start - timedelta(days=1)).isoformat()}",
        f"--before={(end + timedelta(days=1)).isoformat()}",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdout is not None

    current: dict[str, object] | None = None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line:
            continue
        if line.startswith("COMMIT|"):
            if current is not None:
                record = _finalize_commit_record(repo_path.name, current)
                if record is not None and start <= record.authored_at.date() <= end:
                    yield record
            parts = line.split("|", 4)
            current = {
                "commit": parts[1],
                "authored_at": parts[2],
                "author": parts[3],
                "subject": parts[4] if len(parts) > 4 else "",
                "path_changes": [],
            }
            continue
        if current is None or "\t" not in line:
            continue
        added, deleted, path = (line.split("\t", 2) + ["", "", ""])[:3]
        normalized_path = _normalize_path(path)
        if normalized_path:
            current["path_changes"].append((
                normalized_path,
                int(added) if added.isdigit() else 0,
                int(deleted) if deleted.isdigit() else 0,
            ))

    if current is not None:
        record = _finalize_commit_record(repo_path.name, current)
        if record is not None and start <= record.authored_at.date() <= end:
            yield record

    proc.communicate()


def _commit_fact_from_record(record: _RepoCommitRecord) -> GitCommitFact:
    paths = tuple(sorted({path for path, _, _ in record.path_changes}))
    path_roots = tuple(sorted({_path_root(path) for path in paths}))
    lines_added = sum(lines_added for _, lines_added, _ in record.path_changes)
    lines_deleted = sum(lines_deleted for _, _, lines_deleted in record.path_changes)
    return GitCommitFact(
        repo=record.repo,
        commit=record.commit,
        authored_at=record.authored_at,
        author=record.author,
        subject=record.subject,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        lines_changed=lines_added + lines_deleted,
        files_changed=len(paths),
        paths=paths,
        path_roots=path_roots,
    )


def _finalize_commit_record(
    repo: str,
    current: dict[str, object],
) -> _RepoCommitRecord | None:
    try:
        authored_at = datetime.fromisoformat(str(current["authored_at"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    path_changes = tuple(
        sorted(
            (
                (str(path), int(lines_added), int(lines_deleted))
                for path, lines_added, lines_deleted in current.get("path_changes", ())
            ),
            key=lambda item: item[0],
        )
    )
    return _RepoCommitRecord(
        repo=repo,
        commit=str(current["commit"]),
        authored_at=authored_at,
        author=str(current.get("author") or ""),
        subject=str(current.get("subject") or ""),
        path_changes=path_changes,
    )


def _path_root(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        return "unknown"
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return "unknown"
    if parts[0] == "crate" and len(parts) >= 3:
        return parts[2]
    if parts[0] in {"src", "tests"} and len(parts) >= 2:
        return parts[1]
    if parts[0] == "Source" and len(parts) >= 2:
        return parts[1]
    return parts[0]


def _normalize_path(path: str) -> str:
    return path.strip()
