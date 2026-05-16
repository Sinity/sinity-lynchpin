"""Personal-source MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id, json_safe as _json_safe


@app.tool()
def calendar_events(
    start: str | None = None,
    end: str | None = None,
    calendar: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Calendar events from the substrate."""
    from datetime import date as _date

    from lynchpin.substrate.connection import connect, substrate_path

    sql = (
        "SELECT uid, calendar, summary, start_at, end_at, all_day, location, "
        "attendees, description, status FROM calendar_event WHERE 1=1"
    )
    params: list[Any] = []

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "calendar_event")
            if refresh_id is None:
                return []
        sql += " AND refresh_id = ?"
        params.append(refresh_id)

        if start:
            sql += " AND start_at >= ?"
            params.append(_date.fromisoformat(start))
        if end:
            sql += " AND start_at <= ?"
            params.append(_date.fromisoformat(end))
        if calendar:
            sql += " AND calendar = ?"
            params.append(calendar)

        sql += " ORDER BY start_at"
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "uid": r[0],
            "calendar": r[1],
            "summary": r[2],
            "start_at": _json_safe(r[3]),
            "end_at": _json_safe(r[4]),
            "all_day": r[5],
            "location": r[6],
            "attendees": r[7],
            "description": r[8],
            "status": r[9],
        }
        for r in rows
    ]


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
