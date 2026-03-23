"""TrajectorySignal dataclass and high-level loading API.

Per-source iterators live in signal_sources; JSONL artefact loading
infrastructure lives in signal_loader. This module re-exports the
constants and utilities that those submodules need.
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from ..core.projects import ALL_PROJECTS

# Pre-resolved project paths and name lookup -- computed once at import
_PROJECT_RESOLVED_PATHS: list[tuple[str, Path]] = [
    (entry.name, Path(entry.path).expanduser().resolve(strict=False))
    for entry in ALL_PROJECTS.values()
]
_PROJECT_NAMES_SORTED: list[str] = sorted(ALL_PROJECTS, key=len, reverse=True)

DEFAULT_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class TrajectorySignal:
    signal_id: str
    source: str
    kind: str
    start: datetime
    end: datetime
    mode_hint: Optional[str] = None
    project_hint: Optional[str] = None
    app: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    domain: Optional[str] = None
    cwd: Optional[str] = None
    detail: Optional[str] = None
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "signal_id": self.signal_id,
            "source": self.source,
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "mode_hint": self.mode_hint,
            "project_hint": self.project_hint,
            "app": self.app,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "cwd": self.cwd,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def resolve_window(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = DEFAULT_LOOKBACK_DAYS,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    tz = _local_tz()
    end_dt = _as_local(end or now or datetime.now(tz))
    start_dt = _as_local(start) if start else end_dt - timedelta(days=days)
    if start_dt >= end_dt:
        raise ValueError(f"Invalid trajectory window: {start_dt.isoformat()} >= {end_dt.isoformat()}")
    return start_dt, end_dt


def load_signals(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[TrajectorySignal]:
    from .signal_sources import _iter_all_signals

    window_start, window_end = resolve_window(start=start, end=end, days=days)
    signals = list(_iter_all_signals(window_start, window_end))
    signals.sort(key=lambda signal: (signal.start, signal.end, signal.source, signal.signal_id))
    return signals


def iter_signals(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> Iterator[TrajectorySignal]:
    yield from load_signals(start=start, end=end, days=days)


# ---------------------------------------------------------------------------
# Utility functions (used by signal_loader and signal_sources)
# ---------------------------------------------------------------------------


def _parse_optional_dt(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_local(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _as_local(datetime.fromisoformat(text))
    except ValueError:
        return None


def _signal_id(source: str, start: datetime, end: datetime, *parts: object) -> str:
    payload = "|".join(
        [
            source,
            start.isoformat(),
            end.isoformat(),
            *[str(part or "") for part in parts],
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    domain = parsed.netloc.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or None


def _path_from_window_title(title: Optional[str]) -> Optional[str]:
    if not title or "/realm/project/" not in title:
        return None
    marker = "/realm/project/"
    _, suffix = title.split(marker, 1)
    candidate = marker + suffix.split()[0]
    return candidate.rstrip(",:)")


@functools.lru_cache(maxsize=4096)
def _resolve_project_hint(text: str) -> Optional[str]:
    """Cached: resolve a single path/text string to a project name."""
    if not text:
        return None
    if text.startswith("/realm/project/"):
        name = text[len("/realm/project/"):].split("/", 1)[0]
        if name in ALL_PROJECTS:
            return name
    try:
        path = Path(text).expanduser().resolve(strict=False)
    except OSError:
        return None
    for name, project_path in _PROJECT_RESOLVED_PATHS:
        if path == project_path or project_path in path.parents:
            return name
    return None


def _project_hint_from_paths(*values: object) -> Optional[str]:
    for value in values:
        text = _text(value)
        if not text:
            continue
        result = _resolve_project_hint(text)
        if result:
            return result
    return None


def _project_hint_from_text(value: object) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    lowered = text.lower()
    for name in _PROJECT_NAMES_SORTED:
        if name.lower() in lowered:
            return name
    return None


def _as_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_local_tz())
    return value.astimezone(_local_tz())


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc


# Re-export _iter_months from signal_loader for callers that import it from here
def _iter_months(start: datetime, end: datetime) -> Iterator[tuple[int, int]]:
    from .signal_loader import _iter_months as _impl
    yield from _impl(start, end)
