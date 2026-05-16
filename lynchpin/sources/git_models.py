"""Dataclasses for the git source API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class GitCommit:
    date: date
    repo: str
    commit: str
    lines_added: int
    lines_deleted: int
    subject: str


@dataclass
class GitCommitActivity:
    repo: str
    timestamp: datetime


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


@dataclass(frozen=True)
class GitDayActivity:
    date: date
    repo: str
    commit_count: int
    lines_added: int
    lines_deleted: int
    churn: int
    net_loc: int
    ai_coauthored: int
    ai_ratio: float
    human_only: int
    dominant_prefix: str
    commit_burst_count: int
    authors: tuple[str, ...]


@dataclass(frozen=True)
class CommitSession:
    repo: str
    start: datetime
    end: datetime
    commit_count: int
    duration_min: float
    is_burst: bool
    ai_fraction: float
    lines_changed: int


@dataclass
class RepoInfo:
    name: str
    path: Path
    exists: bool
    branch: Optional[str]
    head: Optional[str]
    last_commit_at: Optional[datetime]


@dataclass
class RepoFile:
    repo: str
    relative: str
    absolute: Path
    category: Optional[str]


@dataclass
class RepoCommitSummary:
    repo: str
    sha: str
    author: str
    authored_at: Optional[datetime]
    subject: str


@dataclass
class TokeiLanguageStat:
    language: str
    code: int
    comments: int
    blanks: int


@dataclass
class TokeiReport:
    repo: str
    total_code: int
    total_lines: int
    languages: List[TokeiLanguageStat]


@dataclass(frozen=True)
class _RepoCommitRecord:
    repo: str
    commit: str
    authored_at: datetime
    author: str
    subject: str
    path_changes: tuple[tuple[str, int, int], ...]
