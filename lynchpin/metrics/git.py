"""Git churn, net LoC, and commit density metrics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Sequence


@dataclass(frozen=True)
class GitMetrics:
    """Aggregated git metrics for a time window."""
    commits: int
    lines_added: int
    lines_deleted: int
    net_loc: int
    repos: Dict[str, int]

    @property
    def churn(self) -> int:
        return self.lines_added + self.lines_deleted


def git_summary(commits: Sequence) -> GitMetrics:
    """Compute aggregated git metrics from a sequence of commit objects."""
    total = len(commits)
    added = sum(getattr(commit, "lines_added", 0) or 0 for commit in commits)
    deleted = sum(getattr(commit, "lines_deleted", 0) or 0 for commit in commits)
    repos: Counter = Counter(getattr(commit, "repo", "") or "" for commit in commits)
    return GitMetrics(
        commits=total,
        lines_added=int(added),
        lines_deleted=int(deleted),
        net_loc=int(added) - int(deleted),
        repos={name: count for name, count in repos.items() if name},
    )
