"""Substrate-backed MCP tools: raw SQL access and evidence-graph metadata.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""

import base64
from datetime import date, datetime
from typing import Any, Optional

from lynchpin.mcp.server import app

# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

_DISALLOWED_TOKENS = frozenset(
    {
        "insert",
        "update",
        "delete",
        "drop",
        "create",
        "alter",
        "attach",
        "truncate",
        "vacuum",
        "pragma",
        "install",
        "load",
    }
)

_MAX_ROWS_HARD_CAP = 10_000


def _is_select_only(sql: str) -> bool:
    """Return True if *sql* starts with SELECT or a CTE (WITH … SELECT).

    Also rejects any statement that contains a disallowed keyword token.
    The token check is case-insensitive and word-boundary-aware (split on
    whitespace/punctuation) to avoid false positives on column names such as
    ``updated_at`` or ``created_by``.
    """
    import re

    stripped = sql.strip()
    upper = stripped.upper()

    # Must start with SELECT or a CTE
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False

    # Token scan: split on non-alphanumeric chars, check each word
    tokens = {t.lower() for t in re.split(r"\W+", stripped) if t}
    return not tokens.intersection(_DISALLOWED_TOKENS)


def _json_safe(value: Any) -> Any:
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
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    # int, float, str, bool — JSON-native
    return value


def _latest_refresh_id(conn: Any) -> str | None:
    """Return the most recent refresh_id from substrate_source_status.

    Shared by all view-backed MCP tools to avoid the duplicated
    ``SELECT refresh_id ... ORDER BY recorded_at DESC LIMIT 1``
    pattern (18 copies across views.py and substrate.py as of 2026-05-09).
    """
    row = conn.execute(
        "SELECT refresh_id FROM substrate_source_status "
        "ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@app.tool()
def query_substrate(
    sql: str,
    parameters: list[Any] | None = None,
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Execute a read-only SELECT against the lynchpin substrate.

    Allowed: SELECT statements and CTEs (WITH … SELECT).
    Rejected: any DDL/DML keyword (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER,
    ATTACH, TRUNCATE, VACUUM, PRAGMA, INSTALL, LOAD).

    The connection is opened with ``read_only=True`` so DuckDB enforces the
    constraint at the engine level in addition to the keyword check.

    Returns:
        {
            "columns": ["col1", "col2", ...],
            "rows": [[val, ...], ...],
            "row_count": N,
            "truncated": bool,
        }

    max_rows is capped at 10 000.
    """
    from lynchpin.duck.connection import connect, substrate_path

    if not _is_select_only(sql):
        raise ValueError(
            "Only SELECT statements are permitted. "
            "Detected a disallowed keyword or non-SELECT statement."
        )

    effective_max = min(max_rows, _MAX_ROWS_HARD_CAP)
    params = list(parameters) if parameters else []

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        result = conn.execute(sql, params)
        columns = [desc[0] for desc in result.description]
        # Fetch one extra row to detect truncation without a separate COUNT query
        raw_rows = result.fetchmany(effective_max + 1)

    truncated = len(raw_rows) > effective_max
    rows = raw_rows[:effective_max]

    return {
        "columns": columns,
        "rows": [_json_safe(list(row)) for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


@app.tool()
def list_substrate_tables() -> list[dict[str, Any]]:
    """List substrate tables with their column names and types.

    Returns:
        [{"table": str, "columns": [{"name": str, "type": str}]}]

    Tables are returned in alphabetical order. Only user tables are included
    (information_schema and pg_catalog system tables are excluded).
    """
    from lynchpin.duck.connection import connect, substrate_path

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        table_rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        ).fetchall()

        result = []
        for (table_name,) in table_rows:
            col_rows = conn.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'main'
                  AND table_name = ?
                ORDER BY ordinal_position
                """,
                [table_name],
            ).fetchall()
            result.append(
                {
                    "table": table_name,
                    "columns": [
                        {"name": col_name, "type": data_type}
                        for col_name, data_type in col_rows
                    ],
                }
            )

    return result


@app.tool()
def substrate_readiness_report() -> dict[str, Any]:
    """Aggregate per-source readiness across the latest promote run (Arc E.1).

    One stop: schema version, latest refresh_id, per-source status with
    age-since-last-success, evidence-graph caveats, and the high-level signal
    "is the substrate trustworthy for analysis right now."

    Returns:
        {
            "substrate_version": int,
            "latest_refresh_id": str | None,
            "latest_recorded_at": "ISO datetime" | None,
            "sources": [
                {
                    "source": str,
                    "status": "ok | empty | unavailable | error",
                    "row_count": int,
                    "reason": str | None,
                    "window_start": "YYYY-MM-DD" | None,
                    "window_end": "YYYY-MM-DD" | None,
                    "recorded_at": "ISO datetime",
                }
            ],
            "evidence_graph": {
                "refresh_id": str,
                "node_count": int,
                "edge_count": int,
                "caveats": [...],
                "generated_at": "ISO datetime",
            } | None,
            "summary": {
                "ok": int,           # source count by status
                "empty": int,
                "unavailable": int,
                "error": int,
                "trustworthy": bool, # true iff zero unavailable+error
            }
        }

    Returns {"substrate_version": ..., "latest_refresh_id": null, "sources": [],
    "summary": {...all-zero}} when the substrate has no promote history yet.
    """
    from lynchpin.duck.connection import connect, substrate_path

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        # ── substrate version ──────────────────────────────────────────────
        version_row = conn.execute(
            "SELECT value FROM substrate_meta WHERE key = 'version'"
        ).fetchone()
        substrate_version = int(version_row[0]) if version_row else None

        # ── latest refresh_id ──────────────────────────────────────────────
        latest_row = conn.execute(
            "SELECT refresh_id, recorded_at FROM substrate_source_status "
            "ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()

        if latest_row is None:
            return {
                "substrate_version": substrate_version,
                "latest_refresh_id": None,
                "latest_recorded_at": None,
                "sources": [],
                "evidence_graph": None,
                "summary": {
                    "ok": 0, "empty": 0, "unavailable": 0, "error": 0,
                    "trustworthy": False,
                },
            }

        latest_refresh_id, latest_recorded_at = latest_row

        # ── per-source status for that refresh ─────────────────────────────
        rows = conn.execute(
            "SELECT source, status, reason, row_count, window_start, "
            "window_end, recorded_at "
            "FROM substrate_source_status WHERE refresh_id = ? "
            "ORDER BY source",
            [latest_refresh_id],
        ).fetchall()

        sources = [
            {
                "source": row[0],
                "status": row[1],
                "reason": row[2],
                "row_count": row[3],
                "window_start": _json_safe(row[4]),
                "window_end": _json_safe(row[5]),
                "recorded_at": _json_safe(row[6]),
            }
            for row in rows
        ]

        # ── evidence graph for the same refresh, if present ────────────────
        eg_row = conn.execute(
            "SELECT refresh_id, node_count, edge_count, caveats, generated_at "
            "FROM evidence_graph_build WHERE refresh_id = ?",
            [latest_refresh_id],
        ).fetchone()
        if eg_row is not None:
            import json as _json
            try:
                caveats = _json.loads(eg_row[3]) if eg_row[3] else []
            except (TypeError, ValueError):
                caveats = []
            evidence_graph = {
                "refresh_id": eg_row[0],
                "node_count": eg_row[1],
                "edge_count": eg_row[2],
                "caveats": caveats,
                "generated_at": _json_safe(eg_row[4]),
            }
        else:
            evidence_graph = None

    summary = {"ok": 0, "empty": 0, "unavailable": 0, "error": 0}
    for source in sources:
        s = source["status"]
        if s in summary:
            summary[s] += 1
    summary["trustworthy"] = (
        summary["unavailable"] == 0 and summary["error"] == 0 and len(sources) > 0
    )

    return {
        "substrate_version": substrate_version,
        "latest_refresh_id": latest_refresh_id,
        "latest_recorded_at": _json_safe(latest_recorded_at),
        "sources": sources,
        "evidence_graph": evidence_graph,
        "summary": summary,
    }


@app.tool()
def substrate_source_status(
    refresh_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Per-source readiness rows from the most recent (or specific) promote run.

    Surfaces the outcome of every source in the substrate-promote step:
    ``ok`` (rows present), ``empty`` (source ran but yielded nothing in the
    window — legitimate), ``unavailable`` (source/file missing — typically
    a stale upstream archive), or ``error`` (exception during promote).

    This distinguishes silent failures (e.g., polylogue's session insights
    are stale → ``ai_work_events`` returns 0 with no error) from successful
    promotes that genuinely had nothing to record.

    Parameters:
        refresh_id: exact ID. Default: most recent promote run.
        status:     filter to rows with this status (e.g. "unavailable").

    Returns:
        [{"refresh_id", "source", "status", "reason", "row_count",
          "window_start", "window_end", "recorded_at"}], ordered by source.
    """
    from lynchpin.duck.connection import connect, substrate_path

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []

        sql = (
            "SELECT refresh_id, source, status, reason, row_count, "
            "window_start, window_end, recorded_at "
            "FROM substrate_source_status WHERE refresh_id = ?"
        )
        params: list[Any] = [refresh_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY source"

        rows = conn.execute(sql, params).fetchall()

    cols = [
        "refresh_id", "source", "status", "reason", "row_count",
        "window_start", "window_end", "recorded_at",
    ]
    return [
        {c: _json_safe(v) for c, v in zip(cols, row)}
        for row in rows
    ]


@app.tool()
def list_evidence_graph_builds(
    start: str | None = None,
    end: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """List evidence-graph builds stored in the substrate.

    Wraps ``lynchpin.duck.reader.list_evidence_graph_builds``.

    Parameters:
        start: ISO date string (YYYY-MM-DD) — filter by exact start_date.
        end:   ISO date string (YYYY-MM-DD) — filter by exact end_date.
        mode:  build mode string (e.g. "full").

    Returns:
        [
            {
                "refresh_id": str,
                "start_date": "YYYY-MM-DD",
                "end_date": "YYYY-MM-DD",
                "mode": str | None,
                "projects": [...],
                "node_count": int,
                "edge_count": int,
                "generated_at": "ISO datetime",
                "materialized_at": "ISO datetime | None",
            }
        ]
    """
    from datetime import date as _date

    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import list_evidence_graph_builds as _list_builds

    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        rows = _list_builds(conn, start=start_d, end=end_d, mode=mode)

    return [_json_safe(row) for row in rows]


@app.tool()
def load_evidence_graph_summary(
    refresh_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Hydrate an evidence graph build and return summary statistics.

    Does NOT return the full nodes/edges arrays — use ``query_substrate`` for
    that to control payload size.

    Parameters:
        refresh_id: exact build ID (takes precedence over start/end).
        start:      ISO date string — select most recent build for this start date.
        end:        ISO date string — select most recent build for this end date.

    Returns:
        {
            "build": {refresh_id, start_date, end_date, mode, generated_at},
            "node_kind_counts": {"commit": N, "ai_work_event": N, ...},
            "edge_relation_counts": {"file_overlap": N, ...},
            "project_day_summary": [
                {"project": str, "date_count": int, "total_commits": int}
            ],
        }

    Returns {"error": "no matching build"} when no build exists.
    """
    from datetime import date as _date

    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import load_evidence_graph as _load_graph

    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        graph = _load_graph(conn, refresh_id=refresh_id, start=start_d, end=end_d)

    if graph is None:
        return {"error": "no matching build"}

    node_kind_counts: dict[str, int] = {}
    for node in graph.nodes:
        node_kind_counts[node.kind] = node_kind_counts.get(node.kind, 0) + 1

    edge_relation_counts: dict[str, int] = {}
    for edge in graph.edges:
        edge_relation_counts[edge.relation] = (
            edge_relation_counts.get(edge.relation, 0) + 1
        )

    # project × date summary
    from collections import defaultdict

    proj_dates: dict[str, set[Any]] = defaultdict(set)
    proj_commits: dict[str, int] = defaultdict(int)
    for node in graph.nodes:
        if node.project and node.date:
            proj_dates[node.project].add(node.date)
            if node.kind == "commit":
                proj_commits[node.project] += 1

    project_day_summary = [
        {
            "project": proj,
            "date_count": len(dates),
            "total_commits": proj_commits.get(proj, 0),
        }
        for proj, dates in sorted(proj_dates.items())
    ]

    return {
        "build": {
            "refresh_id": graph.refresh_id if hasattr(graph, "refresh_id") else refresh_id,
            "start_date": _json_safe(graph.start),
            "end_date": _json_safe(graph.end),
            "mode": graph.mode,
            "generated_at": _json_safe(graph.generated_at),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
        },
        "node_kind_counts": node_kind_counts,
        "edge_relation_counts": edge_relation_counts,
        "project_day_summary": project_day_summary,
    }


# ── M.13 AI-Attribution Backfill ─────────────────────────────────────────────


@app.tool()
def ai_attribution_backfill(
    refresh_id: str | None = None,
    time_window_hours: int = 24,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill commit_fact.ai_attribution by matching commits to AI work events.

    Matches commits to ai_work_event rows where:
    - Same project
    - Commit authored_at within ±time_window_hours of work_event start_ts
    - Non-empty file_path intersection between commit.paths and
      ai_work_event.file_paths

    Writes a JSON object to commit_fact.ai_attribution:
    {
        "matched_events": N,
        "top_kinds": ["implementation", ...],
        "matched_via": "file_path_overlap",
        "backfilled_at": "ISO datetime"
    }

    This is an UPDATE — it modifies the substrate. Call with dry_run=True
    to preview without writing.

    Parameters:
        refresh_id:         snapshot to backfill; default = latest.
        time_window_hours:  ± hours window for temporal match (default 24).
        dry_run:            preview matches without writing.

    Returns:
        {
            "matched_commits": int,
            "total_commits": int,
            "match_rate": float,
            "dry_run": bool,
            "top_matches": [{"sha": str, "subject": str, "matched_events": int}],
        }
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    from lynchpin.duck.connection import connect, substrate_path

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {"error": "no promote runs"}

        total = conn.execute(
            "SELECT COUNT(*) FROM commit_fact WHERE refresh_id = ?",
            [refresh_id],
        ).fetchone()[0]

        # Match commits to AI work events by path intersection + time window.
        # ai_work_event.project is often NULL (promoted without resolver), so
        # we match on file_path intersection alone — paths contain project
        # directories so cross-project false positives are rare.
        matches = conn.execute("""
            SELECT c.sha, c.repo, c.subject,
                   COUNT(we.event_id) AS matched_events,
                   ARRAY_AGG(DISTINCT we.kind) AS kinds,
                   ARRAY_AGG(DISTINCT we.event_id) AS event_ids
            FROM commit_fact c
            JOIN ai_work_event we
              ON list_has_any(c.paths, we.file_paths)
             AND ABS(EXTRACT(EPOCH FROM c.authored_at - we.start_ts)) < ?
            WHERE we.start_ts IS NOT NULL
              AND len(c.paths) > 0
              AND len(we.file_paths) > 0
              AND c.refresh_id = ?
            GROUP BY c.sha, c.repo, c.subject
            ORDER BY matched_events DESC
        """, [time_window_hours * 3600, refresh_id]).fetchall()

    if matches is None:
        matches = []

    matched_count = len(matches)
    now_iso = _dt.now(_tz.utc).isoformat()

    if not dry_run:
        with connect(path, read_only=False) as conn:
            for sha, repo, subject, cnt, kinds, event_ids in matches:
                attribution = _json.dumps({
                    "matched_events": cnt,
                    "top_kinds": list(kinds[:5]) if kinds else [],
                    "matched_via": "file_path_overlap",
                    "backfilled_at": now_iso,
                })
                conn.execute(
                    "UPDATE commit_fact SET ai_attribution = ? "
                    "WHERE sha = ? AND repo = ?",
                    [attribution, sha, repo],
                )

    return {
        "matched_commits": matched_count,
        "total_commits": total,
        "match_rate": round(matched_count / max(total, 1), 3),
        "dry_run": dry_run,
        "top_matches": [
            {"sha": r[0][:8], "subject": r[2][:60], "matched_events": r[3]}
            for r in (matches[:10] if matches else [])
        ],
    }
