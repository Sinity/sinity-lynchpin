"""Substrate readers/promoters for Polylogue time-composition rows."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any, Iterable

from lynchpin.substrate._filters import add_date_filter, build_where
from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

_SPAN_COLUMNS = (
    "span_id",
    "session_id",
    "provider",
    "lane",
    "kind",
    "start_ts",
    "end_ts",
    "duration_s",
    "source",
    "role",
    "project",
    "app",
    "summary",
    "tool_names",
    "fidelity",
    "confidence",
    "metadata",
)

_COMPOSITION_COLUMNS = (
    "session_id",
    "provider",
    "title",
    "start_ts",
    "end_ts",
    "status",
    "reason",
    "message_count",
    "wall_seconds",
    "engaged_seconds",
    "span_count",
    "overlap_count",
    "seconds_by_lane",
    "seconds_by_kind",
    "cross_source_seconds",
    "projects",
    "tags",
)

_OVERLAP_COLUMNS = (
    "session_id",
    "primary_span_id",
    "other_span_id",
    "source",
    "lane",
    "kind",
    "start_ts",
    "end_ts",
    "duration_s",
    "project",
    "metadata",
)


def promote_polylogue_timeline_spans(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    def extract(row: Any) -> tuple[Any, ...]:
        return (
            row.span_id,
            row.session_id,
            row.provider,
            row.lane,
            row.kind,
            row.start,
            row.end,
            row.duration_s,
            row.source,
            row.role,
            row.project,
            row.app,
            row.summary,
            list(row.tool_names),
            row.fidelity,
            row.confidence,
            json.dumps(row.metadata, sort_keys=True),
        )

    return promote_rows(
        conn,
        table="polylogue_timeline_span",
        columns=_SPAN_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=extract,
    )


def promote_polylogue_session_compositions(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    def extract(row: Any) -> tuple[Any, ...]:
        return (
            row.session_id,
            row.provider,
            row.title,
            row.start,
            row.end,
            row.status,
            row.reason,
            row.message_count,
            row.wall_seconds,
            row.engaged_seconds,
            row.span_count,
            row.overlap_count,
            json.dumps(row.seconds_by_lane, sort_keys=True),
            json.dumps(row.seconds_by_kind, sort_keys=True),
            json.dumps(row.cross_source_seconds, sort_keys=True),
            list(row.projects),
            list(row.tags),
        )

    return promote_rows(
        conn,
        table="polylogue_session_time_composition",
        columns=_COMPOSITION_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=extract,
    )


def promote_polylogue_cross_source_overlaps(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    def extract(row: Any) -> tuple[Any, ...]:
        return (
            row.session_id,
            row.primary_span_id,
            row.other_span_id,
            row.source,
            row.lane,
            row.kind,
            row.start,
            row.end,
            row.duration_s,
            row.project,
            json.dumps(row.metadata, sort_keys=True),
        )

    return promote_rows(
        conn,
        table="polylogue_cross_source_overlap",
        columns=_OVERLAP_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=extract,
    )


def load_polylogue_session_compositions(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    add_date_filter("start_ts", start, end, clauses, params, nullable=True)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    where = build_where(clauses, params)
    rows = conn.execute(
        f"""
        SELECT session_id, provider, title, start_ts, end_ts, status, reason,
               message_count, wall_seconds, engaged_seconds, span_count,
               overlap_count, seconds_by_lane, seconds_by_kind,
               cross_source_seconds, projects, tags, refresh_id
        FROM polylogue_session_time_composition
        {where}
        ORDER BY start_ts NULLS LAST, session_id
        """,
        params,
    ).fetchall()
    return [
        {
            "session_id": row[0],
            "provider": row[1],
            "title": row[2],
            "start": row[3],
            "end": row[4],
            "status": row[5],
            "reason": row[6],
            "message_count": row[7],
            "wall_seconds": row[8],
            "engaged_seconds": row[9],
            "span_count": row[10],
            "overlap_count": row[11],
            "seconds_by_lane": json.loads(row[12] or "{}"),
            "seconds_by_kind": json.loads(row[13] or "{}"),
            "cross_source_seconds": json.loads(row[14] or "{}"),
            "projects": list(row[15] or []),
            "tags": list(row[16] or []),
            "refresh_id": row[17],
        }
        for row in rows
    ]
