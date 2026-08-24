"""Helpers for parsing WeeChat IRC log files.

Layout (post-2026-04-08 reorg):
    <captures_root>/_raw/<channel>/<YYYY-MM-DD>.log              (live)
    <captures_root>/_raw/<channel>/<YYYY-MM-DD>.b2-<hex>.log     (sealed)

Channel directories carry their original IRC names (``#lesswrong``,
``#lw-politics``, ``libera``). Top-level symlinks (``lesswrong``,
``lw-politics``, ``libera``) point at them with the leading ``#`` stripped
for nicer browsing — these are skipped here to avoid double-counting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional


@dataclass
class LogEntry:
    timestamp: datetime
    nick: str
    message: str
    raw: str
    channel: Optional[str] = None
    source_path: Optional[Path] = None

    @property
    def date_key(self) -> str:
        return self.timestamp.strftime("%Y-%m-%d")

    @property
    def hour(self) -> int:
        return self.timestamp.hour


# ``YYYY-MM-DD`` followed by an optional ``.b2-<hex>`` seal suffix.
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\.b2-[0-9a-f]+)?$")


def captures_root(start: Path) -> Path:
    """Return the captures root for ``start`` (which may be the root itself
    or any subdirectory like ``scripts/``)."""
    start = start.resolve()
    if (start / "_raw").is_dir():
        return start
    if (start.parent / "_raw").is_dir():
        return start.parent
    return start


def iter_log_files(root: Path) -> Iterator[Path]:
    """Yield all per-day .log files under ``root/_raw/<channel>/``.

    Hidden files, top-level symlinks, and pipeline outputs (``_processed``,
    ``_weechat-meta``, ``derived``, etc.) are deliberately not walked.
    """
    raw = root / "_raw"
    if not raw.is_dir():
        return
    for channel_dir in sorted(raw.iterdir()):
        if channel_dir.is_symlink() or not channel_dir.is_dir():
            continue
        for path in sorted(channel_dir.glob("*.log")):
            if path.is_file() and not path.is_symlink():
                yield path


def channel_from_path(path: Path) -> Optional[str]:
    """Extract channel name from a path like ``_raw/<channel>/YYYY-MM-DD.log``.

    Falls back to the legacy ``#channel.MM.DD.log`` filename split for any
    log file that ends up outside ``_raw/`` (e.g., the migration cleanup
    script encountering a stray file).
    """
    parent = path.parent.name
    if parent and parent != "_raw" and not parent.isdigit():
        return parent
    stem = path.name.split(".", 1)[0]
    return stem or None


def file_date(path: Path) -> Optional[date]:
    """Parse the calendar date a log file represents.

    Recognises ``YYYY-MM-DD.log`` (live) and ``YYYY-MM-DD.b2-<hex>.log``
    (sealed). Returns ``None`` for any other shape.
    """
    match = _FILENAME_DATE_RE.match(path.stem)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def parse_line(line: str) -> Optional[LogEntry]:
    """Parse a WeeChat log line into a LogEntry object."""
    parts = line.rstrip("\n").split("\t", 2)
    if len(parts) < 2:
        return None

    timestamp_str, actor = parts[0], parts[1]
    message = parts[2] if len(parts) == 3 else ""

    try:
        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    return LogEntry(timestamp=timestamp, nick=actor, message=message, raw=line)


def iter_entries(paths: Iterable[Path]) -> Iterator[LogEntry]:
    """Iterate over parsed entries from an iterable of log files."""
    for path in paths:
        channel = channel_from_path(path)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                entry = parse_line(line)
                if entry is None:
                    continue
                entry.channel = channel
                entry.source_path = path
                yield entry
