"""Dataclasses for the git source API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class GitCommit:
    date: date
    repo: str
    commit: str
    lines_added: int
    lines_deleted: int
    subject: str

    def __post_init__(self) -> None:
        if self.lines_added < 0:
            raise ValueError(
                f"GitCommit.lines_added ({self.lines_added}) must be >= 0"
            )
        if self.lines_deleted < 0:
            raise ValueError(
                f"GitCommit.lines_deleted ({self.lines_deleted}) must be >= 0"
            )


@dataclass(frozen=True)
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

    def __post_init__(self) -> None:
        if self.lines_added < 0:
            raise ValueError(
                f"GitCommitFact.lines_added ({self.lines_added}) must be >= 0"
            )
        if self.lines_deleted < 0:
            raise ValueError(
                f"GitCommitFact.lines_deleted ({self.lines_deleted}) must be >= 0"
            )
        if self.lines_changed < 0:
            raise ValueError(
                f"GitCommitFact.lines_changed ({self.lines_changed}) must be >= 0"
            )
        if self.files_changed < 0:
            raise ValueError(
                f"GitCommitFact.files_changed ({self.files_changed}) must be >= 0"
            )

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

    def __post_init__(self) -> None:
        if self.lines_added < 0:
            raise ValueError(
                f"GitFileChangeFact.lines_added ({self.lines_added}) must be >= 0"
            )
        if self.lines_deleted < 0:
            raise ValueError(
                f"GitFileChangeFact.lines_deleted ({self.lines_deleted}) must be >= 0"
            )
        if self.lines_changed < 0:
            raise ValueError(
                f"GitFileChangeFact.lines_changed ({self.lines_changed}) must be >= 0"
            )

    @property
    def date(self) -> date:
        return self.authored_at.date()


@dataclass(frozen=True)
class GitPatchExcerpt:
    line_count: int
    truncated: bool
    patch_excerpt: str

    def __post_init__(self) -> None:
        if self.line_count < 0:
            raise ValueError(
                f"GitPatchExcerpt.line_count ({self.line_count}) must be >= 0"
            )


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

    def __post_init__(self) -> None:
        if self.commit_count < 0:
            raise ValueError(
                f"GitDayActivity.commit_count ({self.commit_count}) must be >= 0"
            )
        if self.lines_added < 0:
            raise ValueError(
                f"GitDayActivity.lines_added ({self.lines_added}) must be >= 0"
            )
        if self.lines_deleted < 0:
            raise ValueError(
                f"GitDayActivity.lines_deleted ({self.lines_deleted}) must be >= 0"
            )
        if self.churn < 0:
            raise ValueError(
                f"GitDayActivity.churn ({self.churn}) must be >= 0"
            )
        if self.ai_coauthored < 0:
            raise ValueError(
                f"GitDayActivity.ai_coauthored ({self.ai_coauthored}) must be >= 0"
            )
        if not (0.0 <= self.ai_ratio <= 1.0):
            raise ValueError(
                f"GitDayActivity.ai_ratio ({self.ai_ratio}) must be in [0.0, 1.0]"
            )
        if self.human_only < 0:
            raise ValueError(
                f"GitDayActivity.human_only ({self.human_only}) must be >= 0"
            )
        if self.commit_burst_count < 0:
            raise ValueError(
                f"GitDayActivity.commit_burst_count ({self.commit_burst_count}) must be >= 0"
            )


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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"CommitSession.end ({self.end}) precedes start ({self.start})"
            )
        if self.commit_count < 0:
            raise ValueError(
                f"CommitSession.commit_count ({self.commit_count}) must be >= 0"
            )
        if self.duration_min < 0:
            raise ValueError(
                f"CommitSession.duration_min ({self.duration_min}) must be >= 0"
            )
        if not (0.0 <= self.ai_fraction <= 1.0):
            raise ValueError(
                f"CommitSession.ai_fraction ({self.ai_fraction}) must be in [0.0, 1.0]"
            )
        if self.lines_changed < 0:
            raise ValueError(
                f"CommitSession.lines_changed ({self.lines_changed}) must be >= 0"
            )


@dataclass(frozen=True)
class RepoInfo:
    name: str
    path: Path
    exists: bool
    branch: Optional[str]
    head: Optional[str]
    last_commit_at: Optional[datetime]


@dataclass(frozen=True)
class RepoFile:
    repo: str
    relative: str
    absolute: Path
    category: Optional[str]


@dataclass(frozen=True)
class RepoCommitSummary:
    repo: str
    sha: str
    author: str
    authored_at: Optional[datetime]
    subject: str


@dataclass(frozen=True)
class TokeiLanguageStat:
    language: str
    code: int
    comments: int
    blanks: int

    def __post_init__(self) -> None:
        if self.code < 0:
            raise ValueError(
                f"TokeiLanguageStat.code ({self.code}) must be >= 0"
            )
        if self.comments < 0:
            raise ValueError(
                f"TokeiLanguageStat.comments ({self.comments}) must be >= 0"
            )
        if self.blanks < 0:
            raise ValueError(
                f"TokeiLanguageStat.blanks ({self.blanks}) must be >= 0"
            )


@dataclass(frozen=True)
class TokeiReport:
    repo: str
    total_code: int
    total_lines: int
    languages: List[TokeiLanguageStat]

    def __post_init__(self) -> None:
        if self.total_code < 0:
            raise ValueError(
                f"TokeiReport.total_code ({self.total_code}) must be >= 0"
            )
        if self.total_lines < 0:
            raise ValueError(
                f"TokeiReport.total_lines ({self.total_lines}) must be >= 0"
            )


@dataclass(frozen=True)
class _RepoCommitRecord:
    repo: str
    commit: str
    authored_at: datetime
    author: str
    subject: str
    path_changes: tuple[tuple[str, int, int], ...]
