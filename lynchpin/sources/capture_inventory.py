"""Shallow itemization of capture roots that have no dedicated source module.

Several `activity/*` roots (audio, screenshots, screen recordings) are real,
growing owner-native data with zero Lynchpin visibility today: they never
show up in `available_sources()`, coverage, or any analysis. They do not
warrant a dedicated typed source yet — deep audio/video/image processing is
out of scope here — but "invisible" is worse than "shallow".

This module answers one narrow question per root: what is there, how much,
and over what time span, computed live from the filesystem (file count, total
bytes, earliest/latest mtime). It intentionally does not parse audio, decode
images, or interpret event payloads. Promote a root out of this catalog into
a real typed source module when an analysis actually needs its content — the
`notifications`/`mpris`/`audio-index`/`audio-topology` event lanes made that
jump; see `lynchpin.sources.sinnix_capture_lanes`.

Roots the operator has flagged as dead/unwanted (2026-08-12) are excluded
rather than itemized as live: `input-dynamics` (dead, superseded by
`activity/keylog`), `stability-lab` (dead/being retired), and
`dev/tortoisesvn` (a historical import the operator does not want tracked).
The raw directories are untouched — only this catalog stopped watching them.
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

#: (id, relative path under its subject root -- activity/ for every entry
#: except comms_teams, which lives under comms/ -- kind, note)
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
    file_count: int
    total_bytes: int
    earliest: datetime | None
    latest: datetime | None


def _scan(path: Path) -> tuple[int, int, datetime | None, datetime | None]:
    file_count = 0
    total_bytes = 0
    earliest: float | None = None
    latest: float | None = None
    for entry_root, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                stat = os.stat(os.path.join(entry_root, name))
            except OSError:
                continue
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
    reports ``exists=False`` with zeroed counts, same as any other source's
    availability check.
    """
    # Every registry entry lived under one shared captures_root before the
    # 2026-08-17 subject recut. All but comms_teams moved to activity/;
    # comms_teams is a bounded platform export under accounts/ (comms/ retired 2026-08-24).
    # An explicit override still applies uniformly to every entry (tests rely
    # on this to point the whole registry at one fake tree).
    activity_base = captures_root if captures_root is not None else get_config().data_root / "activity"
    comms_base = captures_root if captures_root is not None else get_config().data_root / "accounts"
    items: list[CaptureInventoryItem] = []
    for item_id, rel_path, kind, note in _REGISTRY:
        base = comms_base if item_id == "comms_teams" else activity_base
        path = base / rel_path
        if not path.exists():
            items.append(
                CaptureInventoryItem(
                    id=item_id,
                    path=path,
                    kind=kind,
                    note=note,
                    exists=False,
                    file_count=0,
                    total_bytes=0,
                    earliest=None,
                    latest=None,
                )
            )
            continue
        file_count, total_bytes, earliest, latest = _scan(path)
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
