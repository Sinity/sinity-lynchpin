"""Commit-level transport helpers built from git history."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime

from ...core.parse import parse_datetime as _parse_dt


def parse_iso_datetime(value: str) -> datetime | None:
    """Kept as a thin wrapper for the existing call sites in
    ``lynchpin.analysis.ecosystem.{work_package_scope,aw_git_join}``;
    delegates to the canonical ``core.parse.parse_datetime``."""
    return _parse_dt(value)


def _path_component(path: str | None) -> str:
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return "unknown"
    parts = [x for x in p.split("/") if x]
    if not parts:
        return "unknown"

    if parts[0] == "crate" and len(parts) >= 3:
        return parts[2]
    if parts[0] in {"src", "tests"} and len(parts) >= 2:
        return parts[1]
    if parts[0] == "Source" and len(parts) >= 2:
        return parts[1]
    return parts[0]


@dataclass
class _CommitAccumulator:
    sha: str
    author: str
    date: str
    subject: str
    additions: int = 0
    deletions: int = 0
    files: set[str] = field(default_factory=set)

    def to_row(self, *, keep_files: bool) -> dict[str, object]:
        row: dict[str, object] = {
            "sha": self.sha,
            "author": self.author,
            "date": self.date,
            "subject": self.subject,
            "additions": self.additions,
            "deletions": self.deletions,
            "files_changed": len(self.files),
            "lines_changed": self.additions + self.deletions,
            "path_roots": sorted({_path_component(path) for path in self.files}),
        }
        if keep_files:
            row["files"] = sorted(self.files)
        return row


def collect_commit_stats(
    repo_dir: str,
    branch: str = "HEAD",
    after: str | None = None,
    before: str | None = None,
    author_allowlist: set[str] | None = None,
    keep_files: bool = False,
) -> list[dict[str, object]]:
    """Collect commit-level stats from `git log --numstat`."""
    cmd: list[str] = ["git", "log", branch, "--pretty=format:COMMIT|%H|%aN|%aI|%s", "--numstat"]
    if after:
        cmd.extend(["--after", after])
    if before:
        cmd.extend(["--before", before])

    proc = subprocess.Popen(
        cmd,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    commits: list[dict[str, object]] = []
    cur: _CommitAccumulator | None = None

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        if author_allowlist and cur.author not in author_allowlist:
            cur = None
            return
        commits.append(cur.to_row(keep_files=keep_files))
        cur = None

    if proc.stdout is None:
        return []

    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line:
            continue

        if line.startswith("COMMIT|"):
            flush()
            parts = line.split("|", 4)
            cur = _CommitAccumulator(
                sha=parts[1],
                author=parts[2],
                date=parts[3],
                subject=parts[4] if len(parts) > 4 else "",
            )
            continue

        if "\t" not in line or not cur:
            continue

        parts = line.split("\t")
        if len(parts) < 3:
            continue

        add = int(parts[0]) if parts[0].isdigit() else 0
        delete = int(parts[1]) if parts[1].isdigit() else 0
        path = parts[2]

        cur.additions += add
        cur.deletions += delete
        cur.files.add(path)

    flush()
    commits.sort(key=lambda c: str(c["date"]))
    return commits
