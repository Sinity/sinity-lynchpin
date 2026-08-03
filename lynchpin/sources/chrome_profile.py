"""Live Chromium-family profile discovery and read-only History access.

Chrome's per-profile ``History`` SQLite holds roughly the trailing ~90 days of
visits and is locked while the browser runs, so every reader here snapshots
the file to a temp copy before opening it. Profile locations are operator
configuration (``LYNCHPIN_CHROME_PROFILE_DBS``, colon-separated paths to
``History`` files); without the override, common XDG profile locations are
probed.

This is a live capture-complement, not an archive: pair it with
``python -m lynchpin.ingest.webhistory`` (which snapshots live profiles into
the webhistory raw inbox) so visits survive Chrome's retention horizon.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

#: Env override: colon-separated absolute paths to Chrome ``History`` files.
CHROME_PROFILE_DBS_ENV = "LYNCHPIN_CHROME_PROFILE_DBS"

#: Chrome stores timestamps as microseconds since 1601-01-01 UTC.
_CHROME_EPOCH_OFFSET_S = 11644473600


def discover_profile_history_dbs() -> list[tuple[Path, str]]:
    """Return ``[(history_db_path, label), ...]`` for live profiles present."""
    env = os.environ.get(CHROME_PROFILE_DBS_ENV)
    if env is not None:
        candidates = [Path(part).expanduser() for part in env.split(":") if part.strip()]
    else:
        home = Path.home()
        candidates = [
            home / ".config/chrome-ws/Default/History",
            home / ".config/google-chrome/Default/History",
            home / ".config/chromium/Default/History",
        ]
    out: list[tuple[Path, str]] = []
    for path in candidates:
        if path.is_file():
            # e.g. ~/.config/chrome-ws/Default/History → "chrome-ws"
            label = path.parent.parent.name or "chrome"
            out.append((path, label))
    return out


@contextmanager
def snapshot_history_db(path: Path) -> Iterator[Path]:
    """Copy the (possibly locked) History DB to a temp file and yield the copy."""
    with tempfile.TemporaryDirectory(prefix="lynchpin-chrome-") as tmp:
        dst = Path(tmp) / "History"
        shutil.copy2(path, dst)
        yield dst


def chrome_time_to_utc(chrome_us: int) -> datetime:
    """Convert Chrome's 1601-epoch microseconds to an aware UTC datetime."""
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=chrome_us / 1_000_000 - _CHROME_EPOCH_OFFSET_S
    )


class ProfileVisit(NamedTuple):
    timestamp: datetime  # aware UTC
    url: str
    title: str
    profile: str


def iter_profile_visits(
    *,
    url_like: Optional[str] = None,
    dbs: Optional[list[tuple[Path, str]]] = None,
) -> Iterator[ProfileVisit]:
    """Yield visits from every discovered live profile (read-only snapshot).

    ``url_like`` is an optional SQL LIKE pattern applied to ``urls.url``.
    """
    for path, label in dbs if dbs is not None else discover_profile_history_dbs():
        with snapshot_history_db(path) as snap:
            conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
            try:
                query = (
                    "SELECT v.visit_time, u.url, COALESCE(u.title, '') "
                    "FROM visits v JOIN urls u ON v.url = u.id"
                )
                params: list[str] = []
                if url_like is not None:
                    query += " WHERE u.url LIKE ?"
                    params.append(url_like)
                for visit_time, url, title in conn.execute(query, params):
                    yield ProfileVisit(
                        timestamp=chrome_time_to_utc(int(visit_time)),
                        url=url,
                        title=title,
                        profile=label,
                    )
            finally:
                conn.close()


__all__ = [
    "CHROME_PROFILE_DBS_ENV",
    "ProfileVisit",
    "chrome_time_to_utc",
    "discover_profile_history_dbs",
    "iter_profile_visits",
    "snapshot_history_db",
]
