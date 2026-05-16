"""Symbol-change table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_in_filter, build_where

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


def load_symbol_changes(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    projects: tuple[str, ...] | None = None,
    paths: tuple[str, ...] | None = None,
    only_breaking: bool = False,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """SELECT ``symbol_change`` rows.

    No lynchpin dataclass exists for symbol changes, so we return
    ``list[dict]`` matching the source-of-truth row shape from
    ``build_active_symbol_changes``. The ``date`` column is a Python
    ``date`` object (DuckDB DATE maps to ``datetime.date`` directly).
    """
    clauses: list[str] = []
    params: list[Any] = []

    if start is not None or end is not None:
        # symbol_change uses a DATE column directly — no cast needed.
        if start is not None and end is not None:
            clauses.append("date BETWEEN ? AND ?")
            params.extend([start, end])
        elif start is not None:
            clauses.append("date >= ?")
            params.append(start)
        else:
            clauses.append("date <= ?")
            params.append(end)

    add_in_filter("project", projects, clauses, params)
    add_in_filter("path", paths, clauses, params)

    if only_breaking:
        clauses.append("breaking_candidate = TRUE")

    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            sha, project, date, path, change_type,
            qualified_name, symbol_kind, exported, breaking_candidate,
            refresh_id
        FROM symbol_change
        {where}
        ORDER BY date, sha, path, qualified_name
    """
    rows = conn.execute(sql, params).fetchall()

    return [
        {
            "sha": sha,
            "project": project,
            "date": row_date,
            "path": path,
            "change_type": change_type,
            "qualified_name": qualified_name,
            "symbol_kind": symbol_kind,
            "exported": exported,
            "breaking_candidate": breaking_candidate,
            "refresh_id": refresh_id_col,
        }
        for (
            sha,
            project,
            row_date,
            path,
            change_type,
            qualified_name,
            symbol_kind,
            exported,
            breaking_candidate,
            refresh_id_col,
        ) in rows
    ]


# ── helpers ───────────────────────────────────────────────────────────────────
# ── commit_fact ───────────────────────────────────────────────────────────────


def promote_symbol_changes(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    """INSERT symbol_change rows from build_active_symbol_changes events[].

    Each mapping must carry: sha, project, date (str), path, change_type,
    qualified_name, symbol_kind, exported, breaking_candidate.

    Missing keys default gracefully so callers can pass the raw dicts from
    the JSON payload without pre-processing.
    """
    conn.execute("DELETE FROM symbol_change WHERE refresh_id = ?", [refresh_id])

    tuples: list[tuple[Any, ...]] = []
    seen: set[tuple[str, str, str]] = set()  # dedupe (sha, path, qualified_name)
    for r in rows:
        sha = r.get("sha") or ""
        project = r.get("project") or ""
        raw_date = r.get("date")
        if isinstance(raw_date, str):
            try:
                row_date = date.fromisoformat(raw_date)
            except ValueError:
                row_date = None
        elif isinstance(raw_date, date):
            row_date = raw_date
        else:
            row_date = None
        if row_date is None:
            continue  # Skip rows without a parseable date.

        key = (sha, r.get("path") or "", r.get("qualified_name") or "")
        if key in seen:
            continue
        seen.add(key)
        tuples.append(
            (
                sha,
                project,
                row_date,
                r.get("path") or "",
                (r.get("change_type") or "").upper() or "M",
                r.get("qualified_name") or "",
                r.get("symbol_kind") or "unknown",
                bool(r.get("exported", False)),
                bool(r.get("breaking_candidate", False)),
                refresh_id,
            )
        )

    if tuples:
        conn.executemany(
            """
            INSERT INTO symbol_change (
                sha, project, date, path, change_type,
                qualified_name, symbol_kind, exported, breaking_candidate,
                refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuples,
        )
    log.debug(
        "promote_symbol_changes: %d rows for refresh_id=%s", len(tuples), refresh_id
    )
    return len(tuples)


__all__ = ["load_symbol_changes", "promote_symbol_changes"]
