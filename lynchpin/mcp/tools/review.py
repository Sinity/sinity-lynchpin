"""Review-oriented MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,
    dataclass_to_json_dict,
    ensure_substrate_materialized_for_read,
)


@app.tool()
def pr_review_rows(
    projects: list[str] | None = None,
    states: list[str] | None = None,
    only_with_friction: bool = False,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read the pr_review_row substrate table.

    Wraps ``lynchpin.substrate.review.load_pr_review_rows``.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.review import load_pr_review_rows

    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    sts: tuple[str, ...] | None = tuple(state.lower() for state in states) if states else None

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="pr_review_rows")
    path = substrate_path()
    with connect(path) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "pr_review_row", caller="pr_review_rows")
            if refresh_id is None:
                return []
        rows = load_pr_review_rows(
            conn,
            projects=projs,
            states=sts,
            only_with_friction=only_with_friction,
            refresh_id=refresh_id,
        )

    return [dataclass_to_json_dict(row) for row in rows]


@app.tool()
def review_bottlenecks(
    min_rounds: int = 2,
    min_review_hours: float = 24.0,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Code review bottleneck detection."""
    from lynchpin.substrate.connection import connect, substrate_path

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="review_bottlenecks")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "pr_review_row", caller="review_bottlenecks")
            if refresh_id is None:
                return []

        rows = conn.execute(
            """
            SELECT project, number, title, url, author,
                   review_round_count,
                   ROUND(time_to_first_review_minutes/60.0, 1) AS review_hours,
                   changes_requested_count, approval_count, friction_signals
            FROM pr_review_row
            WHERE refresh_id = ?
              AND (review_round_count >= ? OR time_to_first_review_minutes >= ?)
            ORDER BY review_round_count DESC, time_to_first_review_minutes DESC
            LIMIT 50
            """,
            [refresh_id, int(min_rounds), float(min_review_hours) * 60],
        ).fetchall()

    return [
        {
            "project": r[0],
            "number": r[1],
            "title": r[2][:80],
            "url": r[3],
            "author": r[4],
            "rounds": r[5],
            "review_hours": r[6],
            "changes_requested": r[7],
            "approvals": r[8],
            "friction_signals": r[9],
        }
        for r in rows
    ]
