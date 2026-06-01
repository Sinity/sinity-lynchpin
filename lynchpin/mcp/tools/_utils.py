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
    """Return the refresh_id that best covers `table`.

    Multiple refresh kinds populate the same fact tables (e.g.
    ``dag:<ts>`` runs full-history; ``current-state:start:end:scope`` runs
    a rolling window with narrower project scope). Picking the most recent
    by ``recorded_at`` silently selects the narrower one and makes
    engineering_throughput / file_hotspots / symbol_velocity look
    "degraded" or empty.

    Ranking among ok-status candidates: row_count in target table DESC,
    then recorded_at DESC as tiebreaker. This prefers comprehensive
    snapshots over recent partial ones.

    Falls back to latest materialized_at with a warning when no
    ``substrate_source_status`` row exists for the table's source.
    """
    import logging

    if not _IDENTIFIER_RE.fullmatch(table):
        raise ValueError(f"invalid substrate table identifier: {table!r}")

    table_to_source = {
        "commit_fact": "commits",
        "file_change_fact": "file_changes",
        "symbol_change": "symbols",
        "evidence_node": "evidence_graph",
        "evidence_edge": "evidence_graph",
        "ai_work_event": "ai_attribution",
        "work_observation": "work_observations",
        "work_observation_stage": "work_observations",
        "work_observation_test_result": "work_observations",
    }
    source_name = table_to_source.get(table, table)

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

    # Rank ok-status candidates by table row count, then by recorded_at.
    # A current-state refresh with narrow project scope (e.g. 5649 file_change
    # rows) loses to a dag refresh with full coverage (65919 rows).
    try:
        candidates = conn.execute(
            "SELECT refresh_id, recorded_at FROM substrate_source_status "
            "WHERE source = ? AND status = 'ok' "
            "ORDER BY recorded_at DESC",
            [source_name],
        ).fetchall()
        if candidates:
            ids = [row[0] for row in candidates]
            recorded_at_by_id = {row[0]: row[1] for row in candidates}
            placeholders = ",".join("?" * len(ids))
            ranked = conn.execute(
                f"SELECT refresh_id, COUNT(*) AS rc FROM {table} "
                f"WHERE refresh_id IN ({placeholders}) "
                f"GROUP BY refresh_id",
                ids,
            ).fetchall()
            if ranked:
                # Sort by row_count DESC, then by recorded_at DESC.
                ranked.sort(
                    key=lambda r: (r[1], recorded_at_by_id.get(r[0])),
                    reverse=True,
                )
                return ranked[0][0]
    except Exception:
        pass

    # Fallback path: no substrate_source_status entry for this source.
    # Rank by row_count DESC then materialized_at DESC — same logic as the
    # ok-status branch above. Picking purely by latest materialized_at here
    # silently selects narrow recent refreshes (e.g., a dag run that only
    # promoted 6 rows for activity_content_day while an earlier
    # current-state run promoted 404) and produces nearly-empty downstream
    # queries.
    row = conn.execute(
        f"SELECT refresh_id, COUNT(*) AS rc FROM {table} "
        f"GROUP BY refresh_id ORDER BY rc DESC, {order_expr} DESC LIMIT 1"
    ).fetchone()

    if row:
        logger = logging.getLogger(__name__)
        logger.warning(
            f"best_refresh_id({table!r}): no refresh_id with source_status "
            f"'{source_name}:ok' found; using highest-coverage refresh "
            f"{row[0]!r} (row_count={row[1]})"
        )
    return row[0] if row else None


def require_best_refresh_id(conn: Any, table: str, *, tool: str) -> str:
    """Return a refresh id or raise instead of pretending missing data is empty."""
    refresh_id = best_refresh_id(conn, table)
    if refresh_id is None:
        raise RuntimeError(
            f"{tool} requires substrate table {table!r}, but no promoted rows exist. "
            "Run `python -m lynchpin.cli.materialize --all --promote --start YYYY-MM-DD --end YYYY-MM-DD` "
            "and inspect `materialization_status` / `substrate_readiness_report`."
        )
    return refresh_id


def registered_tool_names() -> tuple[str, ...]:
    """Return names currently registered on the in-process FastMCP app."""
    from lynchpin.mcp.server import app

    tools = getattr(getattr(app, "_tool_manager", None), "_tools", {})
    if not isinstance(tools, dict):
        return ()
    return tuple(sorted(str(name) for name in tools))
