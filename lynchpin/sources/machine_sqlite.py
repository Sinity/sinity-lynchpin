"""SQLite helpers for live machine telemetry reads."""

from __future__ import annotations

import json
import fcntl
import os
import stat
import re
import sqlite3
from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, BinaryIO

from ..core.errors import SourceUnavailableError



def as_utc(value: str) -> datetime | None:
    try:
        text = value.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


_EXPECTED_SQLITE_INODE: ContextVar[tuple[Path, int] | None] = ContextVar(
    "machine_sqlite_inode", default=None
)


@contextmanager
def expect_sqlite_inode(path: Path, inode: int) -> Iterator[None]:
    """Bind opening a configured segment to the manifest's selected object."""
    token = _EXPECTED_SQLITE_INODE.set((path.absolute(), inode))
    try:
        yield
    finally:
        _EXPECTED_SQLITE_INODE.reset(token)


def machine_lock_path(current: Path) -> Path:
    return current.with_name(current.name + ".segments.lock")


def acquire_machine_read_lock(
    current: Path, *, required: bool = False
) -> BinaryIO | None:
    """The producer creates this file; source readers never write native state."""
    path = machine_lock_path(current)
    try:
        before = path.lstat()
    except FileNotFoundError:
        if required:
            raise SourceUnavailableError(
                "machine", path=str(path), reason="source-set lock is missing"
            )
        return None
    if not stat.S_ISREG(before.st_mode):
        raise SourceUnavailableError(
            "machine", path=str(path), reason="source-set lock is not a regular file"
        )
    held = path.open("rb")
    try:
        opened = os.fstat(held.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SourceUnavailableError(
                "machine", path=str(path), reason="source-set lock was replaced"
            )
        try:
            fcntl.flock(held.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceUnavailableError(
                "machine", path=str(current), reason="SQLite cutover in progress"
            ) from exc
        return held
    except BaseException:
        held.close()
        raise


class _ReadConnection(sqlite3.Connection):
    _source_lock: BinaryIO | None = None

    def close(self) -> None:
        super().close()
        held, self._source_lock = self._source_lock, None
        if held is not None:
            held.close()


def connect_readonly(path: Path) -> sqlite3.Connection:
    expected = _EXPECTED_SQLITE_INODE.get()

    def validate() -> None:
        if expected is None or expected[0] != path.absolute():
            return
        try:
            if path.stat().st_ino != expected[1]:
                raise SourceUnavailableError(
                    "machine",
                    path=str(path),
                    reason="SQLite segment replaced while opening",
                )
        except OSError as exc:
            raise SourceUnavailableError(
                "machine", path=str(path), reason=str(exc)
            ) from exc

    held = acquire_machine_read_lock(path)
    try:
        validate()
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, factory=_ReadConnection
        )
        conn._source_lock = held
        try:
            validate()
        except BaseException:
            conn.close()
            raise
        return conn
    except BaseException:
        if held is not None:
            held.close()
        raise


def has_leading_index(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Whether a complete index can seek the requested leading column."""
    for entry in conn.execute(f"PRAGMA index_list({table})"):
        index = str(entry[1])
        if entry[4] or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", index):
            continue
        columns = [row[2] for row in conn.execute(f"PRAGMA index_info({index})")]
        if columns and columns[0] == column:
            return True
    return False


def time_prefix_index(conn: sqlite3.Connection, table: str) -> tuple[str, str] | None:
    """Find an existing nonnullable prefix/time index without source mutation."""
    nonnull = {row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[3]}
    for entry in conn.execute(f"PRAGMA index_list({table})"):
        index = str(entry[1])
        if entry[4] or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", index):
            continue
        columns = [row[2] for row in conn.execute(f"PRAGMA index_info({index})")]
        if len(columns) >= 2 and columns[1] == "observed_at" and columns[0] in nonnull:
            prefix = columns[0]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prefix):
                return prefix, index
    return None


def _prefixes_sql(table: str, prefix: str, index: str) -> str:
    return (
        f"WITH RECURSIVE prefixes(value) AS ("
        f"SELECT MIN({prefix}) FROM {table} INDEXED BY {index} UNION ALL "
        f"SELECT (SELECT MIN({prefix}) FROM {table} INDEXED BY {index} "
        f"WHERE {prefix}>prefixes.value) FROM prefixes WHERE value IS NOT NULL) "
    )


def latest_observed_at(conn: sqlite3.Connection, table: str) -> str | None:
    """Seek each prefix's last timestamp instead of scanning its full history."""
    indexed = time_prefix_index(conn, table)
    if indexed is None:
        sql = f"SELECT MAX(observed_at) FROM {table}"
    else:
        prefix, index = indexed
        sql = _prefixes_sql(table, prefix, index) + (
            f"SELECT MAX((SELECT MAX(observed_at) FROM {table} INDEXED BY {index} "
            f"WHERE {prefix}=prefixes.value)) FROM prefixes WHERE value IS NOT NULL"
        )
    value = conn.execute(sql).fetchone()[0]
    return str(value) if value is not None else None


def window_select(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: Sequence[str],
    where: list[str],
    order: str,
    prefix: str,
    index: str,
) -> str:
    """Seek each indexed prefix for bounded reads without source statistics writes.

    SQLite otherwise scans these histories when the time column is second in
    the index and ANALYZE statistics are absent. CROSS JOIN keeps the prefix
    enumeration outside the time-range searches; only matching rows are sorted.
    Legacy schemas retain the ordinary query.
    """
    indexed = any(
        row[1] == index and not row[4]
        for row in conn.execute(f"PRAGMA index_list({table})")
    )
    nonnull = any(
        row[1] == prefix and row[3]
        for row in conn.execute(f"PRAGMA table_info({table})")
    )
    index_columns = (
        [row[2] for row in conn.execute(f"PRAGMA index_info({index})")]
        if indexed
        else []
    )
    if where and nonnull and index_columns[:2] == [prefix, "observed_at"]:
        return (
            _prefixes_sql(table, prefix, index)
            + f"SELECT {', '.join(columns)} FROM prefixes CROSS JOIN {table} "
            f"INDEXED BY {index} WHERE prefixes.value IS NOT NULL "
            f"AND {table}.{prefix}=prefixes.value AND {' AND '.join(where)} "
            f"ORDER BY {order}"
        )
    sql = f"SELECT {', '.join(columns)} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql + f" ORDER BY {order}"


def default_route_interface() -> str | None:
    route = Path("/proc/net/route")
    try:
        for line in route.read_text().splitlines()[1:]:
            fields = line.split()
            if (
                len(fields) >= 4
                and fields[1] == "00000000"
                and int(fields[3], 16) & 0x2
            ):
                return fields[0]
    except (OSError, ValueError):
        return None
    return None


def json_obj(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {"_parse_error": True}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
