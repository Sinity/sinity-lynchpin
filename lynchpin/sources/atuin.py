from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from ..core.config import get_config


@dataclass
class AtuinCommand:
    timestamp: datetime
    duration_ns: Optional[int]
    exit_code: Optional[int]
    cwd: Optional[str]
    command: str


def iter_commands(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db_path: Optional[Path] = None,
) -> Iterator[AtuinCommand]:
    cfg = get_config()
    db = Path(db_path).expanduser() if db_path else cfg.atuin_db
    with sqlite3.connect(str(db)) as conn:
        query = "SELECT timestamp, duration, exit, cwd, command FROM history"
        clauses: List[str] = []
        params: List[object] = []
        unit = _detect_unit(conn)
        if start:
            clauses.append("timestamp >= ?")
            params.append(_to_unit(start, unit))
        if end:
            clauses.append("timestamp < ?")
            params.append(_to_unit(end, unit))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp"
        for row in conn.execute(query, params):
            ts = _from_unit(row[0], unit)
            yield AtuinCommand(
                timestamp=ts,
                duration_ns=row[1],
                exit_code=row[2],
                cwd=row[3],
                command=row[4],
            )


def _detect_unit(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT timestamp FROM history ORDER BY timestamp DESC LIMIT 1").fetchone()
    if not row:
        return "s"
    value = int(row[0])
    if value > 10**14:
        return "ns"
    if value > 10**11:
        return "ms"
    return "s"


def _to_unit(dt: datetime, unit: str) -> int:
    ts = dt.astimezone(timezone.utc).timestamp()
    if unit == "ns":
        return int(ts * 1_000_000_000)
    if unit == "ms":
        return int(ts * 1_000)
    return int(ts)


def _from_unit(value: int, unit: str) -> datetime:
    if unit == "ns":
        seconds = value / 1_000_000_000
    elif unit == "ms":
        seconds = value / 1_000
    else:
        seconds = value
    return datetime.fromtimestamp(seconds, tz=timezone.utc)
