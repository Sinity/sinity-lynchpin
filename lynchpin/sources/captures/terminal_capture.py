"""Terminal capture parsing and audit surfaces."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator

from ...core.config import get_config

from .terminal_capture_parsers import (
    _audit_terminal_session,
    _parse_session_fast,
    _parse_terminal_session,
    _parse_terminal_session_events,
    summarize_terminal_audit,
)
from .terminal_capture_support import _iter_cast_paths, _iter_cast_paths_for_window
from .terminal_capture_types import (
    TerminalAuditEntry,
    TerminalAuditSummary,
    TerminalSessionEvent,
    TerminalSessionMetadata,
)

__all__ = [
    "TerminalAuditEntry",
    "TerminalAuditSummary",
    "TerminalSessionEvent",
    "TerminalSessionMetadata",
    "iter_terminal_audit",
    "iter_terminal_session_events",
    "iter_terminal_sessions",
    "iter_terminal_sessions_fast",
    "summarize_terminal_audit",
]

def iter_terminal_sessions(
    root: Path | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[TerminalSessionMetadata]:
    """Scan for terminal session casts and yield normalized session metadata.

    When *start* and *end* are provided, only cast files stored in YYYY/MM/DD
    directories that overlap the window are scanned, giving a large speedup for
    short windows over a large corpus.
    """
    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalSessionMetadata]:
        path_iter = (
            _iter_cast_paths_for_window(scan_root, start, end)
            if start is not None and end is not None
            else _iter_cast_paths(scan_root)
        )
        for cast_path in path_iter:
            meta = _parse_terminal_session(cast_path)
            if meta:
                yield meta

    return generator()


def iter_terminal_session_events(
    root: Path | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[TerminalSessionEvent]:
    """Yield low-frequency terminal session events.

    When *start* and *end* are provided, only cast files stored in YYYY/MM/DD
    directories that overlap the window are scanned.
    """
    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalSessionEvent]:
        path_iter = (
            _iter_cast_paths_for_window(scan_root, start, end)
            if start is not None and end is not None
            else _iter_cast_paths(scan_root)
        )
        for cast_path in path_iter:
            yield from _parse_terminal_session_events(cast_path)

    return generator()


def iter_terminal_audit(root: Path | None = None) -> Iterator[TerminalAuditEntry]:
    """Yield per-session audit entries for the terminal corpus."""

    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalAuditEntry]:
        for cast_path in _iter_cast_paths(scan_root):
            entry = _audit_terminal_session(cast_path)
            if entry:
                yield entry

    return generator()


def iter_terminal_sessions_fast(
    root: Path | None = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[TerminalSessionMetadata]:
    """Fast variant of iter_terminal_sessions that reads only the manifest JSON sidecar.

    Skips the expensive cast-file timing scan (which can read megabytes of cast data).
    All fields needed by the activity-signal pipeline are present in the manifest;
    sessions without a manifest fall back to a minimal parse of the cast header.
    Use this in hot paths where timing accuracy is less critical than speed.
    """
    cfg = get_config()
    scan_root = Path(root) if root else cfg.asciinema_root
    if not scan_root.exists():
        return iter(())

    def generator() -> Iterator[TerminalSessionMetadata]:
        path_iter = (
            _iter_cast_paths_for_window(scan_root, start, end)
            if start is not None and end is not None
            else _iter_cast_paths(scan_root)
        )
        for cast_path in path_iter:
            meta = _parse_session_fast(cast_path)
            if meta:
                yield meta

    return generator()
