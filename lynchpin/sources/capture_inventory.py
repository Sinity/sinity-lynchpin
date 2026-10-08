"""Shallow live inventory for configured captures without dedicated readers.

Counts, byte totals and filesystem dates describe source availability without
parsing audio, images or event payloads. Missing or inaccessible roots retain
unknown counts and their availability reason; accessible empty roots have zero
counts. Managed placement comes from configuration and its exported registry.
Typed event lanes remain owned by ``sinnix_capture_lanes``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ..core.config import get_config

CaptureKind = Literal[
    "audio",
    "image",
    "video",
    "event_lane",
    "historical_archive",
    "reserved_empty",
]

#: (id, configuration lane or explicit-root child, kind, note)
#: Roots already owned by a dedicated source module (activitywatch, arbtt,
#: asciinema/kitty-scrollback via terminal.py, atuin/zsh via shell/terminal,
#: keylog, clipboard, irc, machine, webhistory, polylogue, syslog,
#: screen-frames via sinnix_capture_lanes.screen_frame_events) are
#: deliberately excluded — this catalog covers what nothing else reads yet.
_REGISTRY: tuple[tuple[str, str, CaptureKind, str], ...] = (
    (
        "audio",
        "audio",
        "audio",
        "PipeWire mic/sink-monitor capture (.opus) plus legacy phone-export archive; "
        "continuous capture, not parsed for content",
    ),
    (
        "screenshot",
        "screenshot",
        "image",
        "periodic desktop screenshots (.png); continuous capture, not parsed for content",
    ),
    (
        "replay",
        "replay",
        "video",
        "on-demand screen recordings (.mp4); not transcoded or analyzed",
    ),
    (
        "comms_teams",
        "teams",
        "historical_archive",
        "frozen historical Microsoft Teams log import; not a continuous capture",
    ),
    (
        "a11y",
        "a11y",
        "reserved_empty",
        "provisioned capture root, no data observed yet",
    ),
)


@dataclass(frozen=True)
class CaptureInventoryItem:
    id: str
    path: Path
    kind: CaptureKind
    note: str
    exists: bool
    file_count: int | None
    total_bytes: int | None
    earliest: datetime | None
    latest: datetime | None
    unavailable_reason: str | None = None


def _scan(path: Path) -> tuple[int, int, datetime | None, datetime | None]:
    file_count = 0
    total_bytes = 0
    earliest: float | None = None
    latest: float | None = None
    def fail(error: OSError) -> None:
        raise error

    for entry_root, _dirnames, filenames in os.walk(path, onerror=fail):
        for name in filenames:
            try:
                stat = os.stat(os.path.join(entry_root, name))
            except OSError:
                raise
            file_count += 1
            total_bytes += stat.st_size
            mtime = stat.st_mtime
            earliest = mtime if earliest is None else min(earliest, mtime)
            latest = mtime if latest is None else max(latest, mtime)
    earliest_dt = datetime.fromtimestamp(earliest, tz=timezone.utc) if earliest is not None else None
    latest_dt = datetime.fromtimestamp(latest, tz=timezone.utc) if latest is not None else None
    return file_count, total_bytes, earliest_dt, latest_dt


def capture_inventory(captures_root: Path | None = None) -> tuple[CaptureInventoryItem, ...]:
    """Return one shallow filesystem itemization per unmodeled capture root.

    Never raises for a missing root: a root that has not been provisioned yet
    reports ``exists=False`` with unknown counts and an availability reason.
    """
    # Explicit roots retain the same per-entry children for standalone callers.
    cfg = get_config()
    items: list[CaptureInventoryItem] = []
    for item_id, rel_path, kind, note in _REGISTRY:
        if captures_root is not None:
            path = captures_root / rel_path
        elif item_id == "comms_teams":
            path = cfg.teams_root
        elif item_id == "audio":
            path = cfg.audio_root
        elif item_id == "screenshot":
            path = cfg.screenshot_root
        else:
            path = cfg.capture_path(rel_path)
        try:
            if not path.is_dir():
                raise OSError("configured source is missing or is not a directory")
            file_count, total_bytes, earliest, latest = _scan(path)
        except OSError as exc:
            items.append(
                CaptureInventoryItem(
                    id=item_id,
                    path=path,
                    kind=kind,
                    note=note,
                    exists=False,
                    file_count=None,
                    total_bytes=None,
                    unavailable_reason=str(exc),
                    earliest=None,
                    latest=None,
                )
            )
            continue
        items.append(
            CaptureInventoryItem(
                id=item_id,
                path=path,
                kind=kind,
                note=note,
                exists=True,
                file_count=file_count,
                total_bytes=total_bytes,
                earliest=earliest,
                latest=latest,
            )
        )
    return tuple(items)


def capture_inventory_item(item_id: str, captures_root: Path | None = None) -> CaptureInventoryItem:
    for item in capture_inventory(captures_root):
        if item.id == item_id:
            return item
    raise KeyError(item_id)


__all__ = [
    "CaptureInventoryItem",
    "CaptureKind",
    "capture_inventory",
    "capture_inventory_item",
]
