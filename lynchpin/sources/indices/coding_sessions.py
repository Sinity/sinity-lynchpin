"""Source module for coding session reconstruction output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from ...core.config import get_config


@dataclass
class CodingSession:
    start: str
    end: str
    duration_hours: float
    commit_count: int
    additions: int
    deletions: int
    repos: str  # JSON-encoded list
    commits: str  # JSON-encoded list


def iter_coding_sessions(path: Optional[Path] = None) -> Iterator[CodingSession]:
    """Yield coding sessions from aw_git_join_metrics.json → coding_sessions[]."""
    source = path or _default_path()
    if not source.exists():
        return
    with source.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for session in data.get("coding_sessions", []):
        yield CodingSession(
            start=session.get("start", ""),
            end=session.get("end", ""),
            duration_hours=float(session.get("duration_hours", 0)),
            commit_count=int(session.get("commit_count", 0)),
            additions=int(session.get("additions", 0)),
            deletions=int(session.get("deletions", 0)),
            repos=json.dumps(session.get("repos", [])),
            commits=json.dumps(session.get("commits", [])),
        )


def _default_path() -> Path:
    cfg = get_config()
    return cfg.repo_root / "artefacts" / "analysis" / "derived" / "aw_git_join_metrics.json"
