"""Shared utilities for MCP tool modules.

Keep helpers that are imported by multiple tool files here to avoid
circular coupling between substrate.py and views.py.
"""

from __future__ import annotations

import base64
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from lynchpin.substrate import snapshots as _snapshots


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


def half_open_date_window(
    start: date | None,
    end: date | None,
) -> tuple[date, date] | None:
    """Convert public inclusive date bounds to a materialization window."""

    if start is None or end is None:
        return None
    return (start, end + timedelta(days=1))


def ensure_substrate_materialized_for_read(
    *,
    caller: str,
    window: tuple[date, date] | None = None,
) -> dict[str, Any]:
    """Cheaply observe/converge the substrate product before a read.

    This deliberately does not enqueue work or hide a full promotion inside
    normal MCP reads. ``evidence_graph_substrate`` is a derived substrate product:
    if the existing DuckDB substrate is usable this returns ``ready``; if not,
    the materialization layer reports why the product cannot be advanced locally.
    """

    from lynchpin.materialization import ensure_materialized

    result = ensure_materialized("evidence_graph_substrate", window=window)
    payload = result.to_json()
    payload["caller"] = caller
    return payload


def pinned_materialization_for_read(*, caller: str, refresh_id: str) -> dict[str, Any]:
    """Return explanatory materialization metadata for explicit snapshot reads."""
    return {
        "name": "evidence_graph_substrate",
        "status": "pinned",
        "changed": False,
        "caller": caller,
        "refresh_id": refresh_id,
    }


def latest_materialized_refresh_id(
    conn: Any,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> str | None:
    """Return the latest materialized substrate refresh_id."""

    return _snapshots.latest_materialized_refresh_id(
        conn,
        caller=caller,
        ledger_path=ledger_path,
    )


def best_materialized_refresh_id(
    conn: Any,
    table: str,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> str | None:
    """Return the highest-coverage materialized refresh_id for a table."""

    return _snapshots.best_materialized_refresh_id(
        conn,
        table,
        caller=caller,
        ledger_path=ledger_path,
    )


def require_best_materialized_refresh_id(
    conn: Any,
    table: str,
    *,
    caller: str,
    tool: str,
    ledger_path: Path | None = None,
) -> str:
    """Return the best materialized refresh id or raise when no data exists."""

    return _snapshots.require_best_materialized_refresh_id(
        conn,
        table,
        caller=caller,
        tool=tool,
        ledger_path=ledger_path,
    )


def registered_tool_names() -> tuple[str, ...]:
    """Return names currently registered on the in-process FastMCP app."""
    from lynchpin.mcp.server import app

    tools = getattr(getattr(app, "_tool_manager", None), "_tools", {})
    if not isinstance(tools, dict):
        return ()
    return tuple(sorted(str(name) for name in tools))
