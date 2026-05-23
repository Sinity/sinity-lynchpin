"""Personal-source MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id, json_safe as _json_safe


@app.tool()
def spotify_daily(
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Daily Spotify listening stats from the spotify_daily table."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "spotify_daily")
            if refresh_id is None:
                return []

        sql = (
            "SELECT date, track_count, minutes_played, unique_artists, "
            "unique_tracks, top_artists, top_tracks FROM spotify_daily "
            "WHERE refresh_id = ?"
        )
        params: list[Any] = [refresh_id]
        if start:
            sql += " AND date >= ?"
            params.append(_date.fromisoformat(start))
        if end:
            sql += " AND date <= ?"
            params.append(_date.fromisoformat(end))
        sql += " ORDER BY date"

        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "date": _json_safe(r[0]),
            "track_count": r[1],
            "minutes_played": r[2],
            "unique_artists": r[3],
            "unique_tracks": r[4],
            "top_artists": r[5],
            "top_tracks": r[6],
        }
        for r in rows
    ]
