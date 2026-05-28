"""SVN commit log source — historical workplace period (2017-07 → 2022-09).

Parses ``svn log --xml`` output from an external TortoiseSVN backup root.

Three unique repository logs exist (trunk, B_2V0 branch, V_3_31 branch).
Revisions are deduplicated across logs.

The operator's SVN username is ``michab`` (920 commits over ~5 years).
Only michab commits are returned by default; pass ``author=None`` for all.

Data: externally configured or supplied through ``root``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.coverage import CoverageBounds
from ..core.errors import SourceUnavailableError
from ..core.primitives import logical_date

SVN_DATA_ROOT = Path("/realm/data/captures/dev/tortoisesvn/historical/jbr_tar")

@dataclass(frozen=True)
class SVNPathChange:
    """One file touched in a commit."""

    path: str
    action: str  # M=modified, A=added, D=deleted, R=replaced
    kind: str = "file"  # file or dir


@dataclass(frozen=True)
class SVNCommit:
    """One SVN commit."""

    revision: int
    author: str
    date: datetime
    message: str
    paths: tuple[SVNPathChange, ...]

    @property
    def file_count(self) -> int:
        return len(self.paths)

    @property
    def added(self) -> int:
        return sum(1 for p in self.paths if p.action == "A")

    @property
    def modified(self) -> int:
        return sum(1 for p in self.paths if p.action == "M")

    @property
    def deleted(self) -> int:
        return sum(1 for p in self.paths if p.action == "D")


@dataclass(frozen=True)
class SVNDayActivity:
    """Per-day SVN commit activity."""

    date: date
    commit_count: int
    files_changed: int
    files_added: int
    files_modified: int
    files_deleted: int
    revisions: tuple[int, ...]
    messages: tuple[str, ...]


def _parse_date(s: str) -> datetime:
    """Parse SVN ISO date. Examples seen: 2022-09-22T10:19:41.611821Z"""
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def iter_commits(
    *,
    author: Optional[str] = "michab",
    root: Optional[Path] = None,
    deduplicate: bool = True,
    start: Optional[date | datetime] = None,
    end: Optional[date | datetime] = None,
) -> Iterator[SVNCommit]:
    """Iterate SVN commits from the historical-workplace repository logs.

    Args:
        author: filter by SVN username. Pass None for all authors.
                The operator is ``michab`` (920 commits 2017-2022).
        root: override data root (default: jbr_tar/).
        deduplicate: skip revisions already seen (multiple logs overlap).
        start: optional inclusive lower day bound.
        end: optional inclusive upper day bound.

    Yields:
        SVNCommit in revision-descending order (most recent first,
        matching the XML source order).
    """
    seen_revisions: set[int] = set()
    log_root = SVN_DATA_ROOT if root is None else Path(root)
    log_paths = sorted(log_root.rglob("svn.log"))
    start_day = _day_key(start)
    end_day = _day_key(end)

    for log_path in log_paths:
        if not log_path.exists():
            continue

        for entry in _parse_log_xml(log_path):
            commit_day = entry["date"].strftime("%Y-%m-%d")
            if start_day is not None and commit_day < start_day:
                break
            if end_day is not None and commit_day > end_day:
                continue

            if deduplicate and entry["revision"] in seen_revisions:
                continue
            seen_revisions.add(entry["revision"])

            if author is not None and entry["author"] != author:
                continue

            yield SVNCommit(
                revision=entry["revision"],
                author=entry["author"],
                date=entry["date"],
                message=entry["message"],
                paths=tuple(
                    SVNPathChange(path=p["path"], action=p["action"], kind=p.get("kind", "file"))
                    for p in entry["paths"]
                ),
            )


def _day_key(value: Optional[date | datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d")


def _parse_log_xml(path: Path) -> Iterator[dict[str, Any]]:
    """Parse svn log --xml format via iterparse.

    Each yielded dict has: revision, author, date, message, paths.
    """
    try:
        for event, elem in ET.iterparse(str(path), events=("end",)):
            if elem.tag != "logentry":
                continue

            revision = int(elem.get("revision", 0))

            author_el = elem.find("author")
            author = author_el.text if author_el is not None and author_el.text else "unknown"

            date_el = elem.find("date")
            date = _parse_date(date_el.text) if date_el is not None and date_el.text else None

            msg_el = elem.find("msg")
            message = msg_el.text if msg_el is not None and msg_el.text else ""

            paths = []
            paths_el = elem.find("paths")
            if paths_el is not None:
                for path_el in paths_el.findall("path"):
                    paths.append({
                        "path": path_el.text or "",
                        "action": path_el.get("action", "M"),
                        "kind": path_el.get("kind", "file"),
                    })

            if date is not None:
                yield {
                    "revision": revision,
                    "author": author,
                    "date": date,
                    "message": message.strip(),
                    "paths": paths,
                }

            elem.clear()

    except ET.ParseError as e:
        import sys
        print(f"SVN XML parse error in {path}: {e}", file=sys.stderr)


def daily_activity(
    *,
    start: date,
    end: date,
    author: Optional[str] = "michab",
) -> list[SVNDayActivity]:
    """Daily SVN commit activity. Compatible with git.daily_activity() shape.

    Returns one row per day with commits, ordered by date ascending.
    """
    buckets: dict[date, list[SVNCommit]] = defaultdict(list)

    for commit in iter_commits(author=author, start=start, end=end):
        buckets[logical_date(commit.date)].append(commit)

    result = []
    for day in sorted(buckets):
        commits = buckets[day]
        result.append(
            SVNDayActivity(
                date=day,
                commit_count=len(commits),
                files_changed=sum(c.file_count for c in commits),
                files_added=sum(c.added for c in commits),
                files_modified=sum(c.modified for c in commits),
                files_deleted=sum(c.deleted for c in commits),
                revisions=tuple(c.revision for c in commits),
                messages=tuple(c.message for c in commits),
            )
        )

    return result


def coverage_bounds() -> CoverageBounds | None:
    if not SVN_DATA_ROOT.exists():
        return None
    try:
        first_dt, last_dt = date_range()
    except SourceUnavailableError:
        return None
    return CoverageBounds(
        source="svn",
        first=first_dt.date(),
        last=last_dt.date(),
        kind="capture",
    )


def date_range(author: Optional[str] = "michab") -> tuple[datetime, datetime]:
    """Oldest and newest commit dates for an author (iterates all logs)."""
    oldest: Optional[datetime] = None
    newest: Optional[datetime] = None
    for commit in iter_commits(author=author):
        if oldest is None or commit.date < oldest:
            oldest = commit.date
        if newest is None or commit.date > newest:
            newest = commit.date
    if oldest is None or newest is None:
        raise SourceUnavailableError("svn", reason=f"No commits for author={author}")
    return oldest, newest


def author_stats(
    *,
    start: Optional[date | datetime] = None,
    end: Optional[date | datetime] = None,
) -> dict[str, int]:
    """Commit count per author across all logs."""
    counts: dict[str, int] = defaultdict(int)
    seen: set[int] = set()
    for commit in iter_commits(author=None, start=start, end=end):
        if commit.revision not in seen:
            seen.add(commit.revision)
            counts[commit.author] += 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


__all__ = [
    "SVNCommit",
    "SVNPathChange",
    "SVNDayActivity",
    "iter_commits",
    "daily_activity",
    "coverage_bounds",
    "date_range",
    "author_stats",
]
