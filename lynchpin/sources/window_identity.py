"""Read Hyprland window-identity captures produced by sinnix's aw-window-identity sidecar.

The aw-window-identity systemd user service (in sinnix's activitywatch
module) writes one JSONL record per Hyprland active-window or title change,
carrying the fields awatcher dropped:

  - ``address``: Hyprland's per-window stable identifier
  - ``pid``: process id
  - ``class``: WM_CLASS-equivalent (= awatcher's ``app``)
  - ``title``: matches awatcher's ``title`` at the same moment

Layout: ``/realm/data/captures/activitywatch/window-identity/<host>-<date>.jsonl``.
Strictly forward-only — older sessions never had this data, so the iterator
silently yields nothing for dates before the sidecar was deployed.

Join recipe (paired with ``window_session_attribution`` for non-Hyprland
hosts):

  for each AW focus span (start, end, app, title):
      candidates = [r for r in iter_window_identity(start, end)
                    if r.host == host
                    and r.class_ == app
                    and r.title == title
                    and start <= r.ts <= end]
      → attach address/pid from the closest record by ts

This is a *strict-better* signal than the polylogue×title heuristic when
the host is Hyprland: the address/pid are observed directly by the
compositor, not inferred.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ..core.config import get_config

__all__ = [
    "WindowIdentityEvent",
    "iter_window_identity",
    "window_identity_root",
]


@dataclass(frozen=True)
class WindowIdentityEvent:
    ts: datetime
    host: str
    event: str
    address: str | None
    pid: int | None
    class_: str | None  # ``class`` is reserved; trailing underscore by convention
    title: str | None
    workspace: str | None
    monitor: str | None
    floating: bool | None
    fullscreen: bool | None


def window_identity_root() -> Path:
    """Default capture root. Override per-installation via lynchpin config."""
    return get_config().captures_root / "activitywatch/window-identity"


def iter_window_identity(
    *,
    start: date | None = None,
    end: date | None = None,
    host: str | None = None,
    root: Path | None = None,
) -> Iterator[WindowIdentityEvent]:
    """Yield WindowIdentityEvents in time order within [start, end].

    Both bounds inclusive. ``host`` filters to a specific machine (matches
    the per-file ``<host>-<date>.jsonl`` naming). Missing capture root or
    no matching files yields an empty iterator — sidecar may not be
    deployed on this machine yet.
    """
    base = root or window_identity_root()
    if not base.exists():
        return

    start_d = start if start is not None else date(1970, 1, 1)
    end_d = end if end is not None else date(2999, 12, 31)

    for path in sorted(base.glob("*.jsonl")):
        # Filename: ``<host>-<YYYY-MM-DD>.jsonl``. The hostname can contain
        # ``-`` (e.g. "sinnix-prime"), so strip the trailing 10-char date
        # then take the rest as the host.
        stem = path.stem
        file_host: str | None = None
        file_date: date | None = None
        if len(stem) > 11 and stem[-11] == "-":
            tail = stem[-10:]
            try:
                file_date = date.fromisoformat(tail)
                file_host = stem[:-11]
            except ValueError:
                file_date = None
        if host is not None and file_host is not None and file_host != host:
            continue
        if file_date is not None and (file_date < start_d or file_date > end_d):
            continue
        yield from _iter_file(path, start=start_d, end=end_d)


def _iter_file(path: Path, *, start: date, end: date) -> Iterator[WindowIdentityEvent]:
    # +/- one day to handle UTC vs local edge: filenames are UTC-dated but
    # the operator may query in local time. Cheap and prevents off-by-one.
    start_dt = datetime.combine(start - timedelta(days=1), datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.max.time())
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = payload.get("ts")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            # Compare in naive form for the cheap bound check, then keep tz.
            ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
            if ts_naive < start_dt or ts_naive > end_dt:
                continue
            yield WindowIdentityEvent(
                ts=ts,
                host=str(payload.get("host") or ""),
                event=str(payload.get("event") or ""),
                address=_str_or_none(payload.get("address")),
                pid=_int_or_none(payload.get("pid")),
                class_=_str_or_none(payload.get("class")),
                title=_str_or_none(payload.get("title")),
                workspace=_str_or_none(payload.get("workspace")),
                monitor=_str_or_none(payload.get("monitor")),
                floating=_bool_or_none(payload.get("floating")),
                fullscreen=_bool_or_none(payload.get("fullscreen")),
            )


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        # bool is a subclass of int — Hyprland encodes some flags numerically
        # but ``pid`` shouldn't ever be bool; reject defensively.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _bool_or_none(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
