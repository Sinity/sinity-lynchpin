"""Processed git activity views — daily aggregates and commit sessions."""

from __future__ import annotations

import logging
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ..indices.gitstats import GitCommit, iter_commits

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path("/realm/project")

# Conventional commit prefixes we recognise
_KNOWN_PREFIXES = frozenset(
    {"feat", "fix", "refactor", "test", "docs", "chore", "perf", "ci", "build", "style"}
)


def _parse_prefix(subject: str) -> str:
    """Extract conventional commit prefix from subject line."""
    for sep in (":", "("):
        idx = subject.find(sep)
        if idx > 0:
            candidate = subject[:idx].strip().lower()
            if candidate in _KNOWN_PREFIXES:
                return candidate
    return "other"


def _count_bursts(timestamps: list[datetime]) -> int:
    """Count clusters of 3+ commits within 5 minutes."""
    if len(timestamps) < 3:
        return 0
    timestamps = sorted(timestamps)
    bursts = 0
    i = 0
    while i < len(timestamps):
        j = i + 1
        while j < len(timestamps) and (timestamps[j] - timestamps[i]) <= timedelta(minutes=5):
            j += 1
        cluster_size = j - i
        if cluster_size >= 3:
            bursts += 1
            i = j
        else:
            i += 1
    return bursts


def _repo_path(repo: str) -> Path:
    """Resolve repo string (may be full path or bare name) to a Path."""
    p = Path(repo)
    if p.is_absolute():
        return p
    return _PROJECT_ROOT / repo


def _fetch_coauthor_info(
    repo: str, after: date, before: date
) -> dict[str, list[str]]:
    """Run git log to get Co-Authored-By data per commit hash.

    Returns mapping of commit_hash -> list of model/author names from trailers.
    """
    repo_path = _repo_path(repo)
    if not (repo_path / ".git").is_dir():
        return {}

    # after is inclusive, before is exclusive (next day)
    cmd = [
        "git", "-C", str(repo_path), "log",
        "--format=%H%n%b%n---END---",
        f"--after={after.isoformat()}",
        f"--before={(before + timedelta(days=1)).isoformat()}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}
    if result.returncode != 0:
        return {}

    coauthors: dict[str, list[str]] = {}
    current_hash: str | None = None
    body_lines: list[str] = []

    for line in result.stdout.splitlines():
        if line == "---END---":
            if current_hash is not None:
                names = []
                for bl in body_lines:
                    stripped = bl.strip()
                    if stripped.lower().startswith("co-authored-by:"):
                        # Extract name from "Co-Authored-By: Name <email>"
                        rest = stripped.split(":", 1)[1].strip()
                        name = rest.split("<")[0].strip() if "<" in rest else rest
                        if name:
                            names.append(name)
                if names:
                    coauthors[current_hash] = names
            current_hash = None
            body_lines = []
        elif current_hash is None and len(line) >= 7 and all(c in "0123456789abcdef" for c in line[:7]):
            current_hash = line.strip()
            body_lines = []
        elif current_hash is not None:
            body_lines.append(line)

    return coauthors


def _fetch_commit_timestamps(
    repo: str, commit_hashes: set[str]
) -> dict[str, datetime]:
    """Get ISO timestamps for specific commit hashes."""
    if not commit_hashes:
        return {}
    repo_path = _repo_path(repo)
    if not (repo_path / ".git").is_dir():
        return {}

    cmd = [
        "git", "-C", str(repo_path), "log",
        "--format=%H %aI",
        "--all",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}
    if result.returncode != 0:
        return {}

    timestamps: dict[str, datetime] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) != 2:
            continue
        sha, iso = parts
        if sha in commit_hashes:
            try:
                ts = iso
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                timestamps[sha] = datetime.fromisoformat(ts)
            except ValueError:
                pass
    return timestamps


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


def iter_git_daily(*, start: date, end: date) -> Iterator[GitDayActivity]:
    """Yield per-repo daily git activity summaries."""
    # Collect commits in range, grouped by (date, repo)
    groups: dict[tuple[date, str], list[GitCommit]] = defaultdict(list)
    for commit in iter_commits():
        if start <= commit.date <= end:
            groups[(commit.date, commit.repo)].append(commit)

    # Fetch co-author info per repo for the full date range
    repos_in_range = {repo for (_, repo) in groups}
    coauthor_cache: dict[str, dict[str, list[str]]] = {}
    for repo in repos_in_range:
        coauthor_cache[repo] = _fetch_coauthor_info(repo, start, end)

    # Fetch timestamps for burst detection
    all_hashes_by_repo: dict[str, set[str]] = defaultdict(set)
    for (_, repo), commits in groups.items():
        for c in commits:
            if c.commit:
                all_hashes_by_repo[repo].add(c.commit)

    ts_cache: dict[str, dict[str, datetime]] = {}
    for repo, hashes in all_hashes_by_repo.items():
        ts_cache[repo] = _fetch_commit_timestamps(repo, hashes)

    for (d, repo), commits in sorted(groups.items()):
        added = sum(c.lines_added for c in commits)
        deleted = sum(c.lines_deleted for c in commits)
        coauthors = coauthor_cache.get(repo, {})

        ai_count = 0
        all_authors: set[str] = set()
        prefixes: list[str] = []
        day_timestamps: list[datetime] = []

        for c in commits:
            if c.commit in coauthors:
                ai_count += 1
                all_authors.update(coauthors[c.commit])
            prefixes.append(_parse_prefix(c.subject))
            ts = ts_cache.get(repo, {}).get(c.commit)
            if ts:
                day_timestamps.append(ts)

        prefix_counts = Counter(prefixes)
        dominant = prefix_counts.most_common(1)[0][0] if prefix_counts else "other"
        total = len(commits)
        human_only = total - ai_count

        yield GitDayActivity(
            date=d,
            repo=repo,
            commit_count=total,
            lines_added=added,
            lines_deleted=deleted,
            churn=added + deleted,
            net_loc=added - deleted,
            ai_coauthored=ai_count,
            ai_ratio=ai_count / total if total else 0.0,
            human_only=human_only,
            dominant_prefix=dominant,
            commit_burst_count=_count_bursts(day_timestamps),
            authors=tuple(sorted(all_authors)),
        )


@dataclass(frozen=True)
class CommitSession:
    repo: str
    start: datetime
    end: datetime
    commits: int
    is_burst: bool
    ai_fraction: float
    lines_changed: int


def iter_commit_sessions(
    *, start: date, end: date, gap_minutes: float = 30
) -> Iterator[CommitSession]:
    """Group commits across repos into temporal sessions."""
    # Collect all commits in date range with their metadata
    commits_in_range: list[GitCommit] = []
    for commit in iter_commits():
        if start <= commit.date <= end:
            commits_in_range.append(commit)

    if not commits_in_range:
        return

    # Fetch coauthor info and timestamps per repo
    repos = {c.repo for c in commits_in_range}
    coauthor_cache: dict[str, dict[str, list[str]]] = {}
    for repo in repos:
        coauthor_cache[repo] = _fetch_coauthor_info(repo, start, end)

    hashes_by_repo: dict[str, set[str]] = defaultdict(set)
    for c in commits_in_range:
        if c.commit:
            hashes_by_repo[c.repo].add(c.commit)

    ts_cache: dict[str, dict[str, datetime]] = {}
    for repo, hashes in hashes_by_repo.items():
        ts_cache[repo] = _fetch_commit_timestamps(repo, hashes)

    # Build (timestamp, commit) pairs, sorted by time
    timed: list[tuple[datetime, GitCommit]] = []
    for c in commits_in_range:
        ts = ts_cache.get(c.repo, {}).get(c.commit)
        if ts:
            timed.append((ts, c))

    if not timed:
        return

    timed.sort(key=lambda x: x[0])
    gap = timedelta(minutes=gap_minutes)

    # Group into sessions
    session: list[tuple[datetime, GitCommit]] = [timed[0]]
    for ts, commit in timed[1:]:
        if ts - session[-1][0] <= gap:
            session.append((ts, commit))
        else:
            yield _build_session(session, coauthor_cache)
            session = [(ts, commit)]
    if session:
        yield _build_session(session, coauthor_cache)


def _build_session(
    entries: list[tuple[datetime, GitCommit]],
    coauthor_cache: dict[str, dict[str, list[str]]],
) -> CommitSession:
    """Build a CommitSession from a cluster of timestamped commits."""
    start_ts = entries[0][0]
    end_ts = entries[-1][0]
    total = len(entries)

    ai_count = 0
    lines = 0
    repo_counts: Counter[str] = Counter()

    for _, c in entries:
        lines += c.lines_added + c.lines_deleted
        repo_counts[c.repo] += 1
        if c.commit in coauthor_cache.get(c.repo, {}):
            ai_count += 1

    dominant_repo = repo_counts.most_common(1)[0][0]
    duration = end_ts - start_ts
    is_burst = total >= 3 and duration < timedelta(minutes=5)

    return CommitSession(
        repo=dominant_repo,
        start=start_ts,
        end=end_ts,
        commits=total,
        is_burst=is_burst,
        ai_fraction=ai_count / total if total else 0.0,
        lines_changed=lines,
    )
