"""Shared utilities for MCP tool modules.

Keep helpers that are imported by multiple tool files here to avoid
circular coupling between substrate.py and views.py.
"""

from __future__ import annotations

import base64
import re
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def json_safe(value: Any) -> Any:
    """Recursively convert a DuckDB result value to a JSON-serialisable type."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    return value


def dataclass_to_json_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance to a JSON-serialisable dict."""
    d = asdict(obj)
    return {k: json_safe(v) for k, v in d.items()}


def latest_refresh_id(conn: Any) -> str | None:
    """Return the most recent refresh_id from substrate_source_status.

    Shared by all view-backed MCP tools to avoid the duplicated
    ``SELECT refresh_id ... ORDER BY recorded_at DESC LIMIT 1``
    pattern (22 copies across views.py and substrate.py as of 2026-05-09).
    """
    row = conn.execute(
        "SELECT refresh_id FROM substrate_source_status "
        "ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def best_refresh_id(conn: Any, table: str) -> str | None:
    """Return the most recent refresh_id that has rows in `table`.

    Unlike latest_refresh_id (which reads substrate_source_status), this
    queries the target table directly. Fixes the refresh_id mismatch where
    domain tables and evidence nodes use different promote runs.
    """
    if not _IDENTIFIER_RE.fullmatch(table):
        raise ValueError(f"invalid substrate table identifier: {table!r}")

    columns = {
        str(row[0])
        for row in conn.execute(f"DESCRIBE {table}").fetchall()
    }
    if "materialized_at" in columns:
        order_expr = "MAX(materialized_at)"
    elif "recorded_at" in columns:
        order_expr = "MAX(recorded_at)"
    elif "date" in columns:
        order_expr = "MAX(date), COUNT(*)"
    else:
        order_expr = "refresh_id"
    row = conn.execute(
        f"SELECT refresh_id FROM {table} "
        f"GROUP BY refresh_id ORDER BY {order_expr} DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None
