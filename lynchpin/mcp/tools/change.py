"""Code-change and churn MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,
    ensure_substrate_materialized_for_read,
    json_safe as _json_safe,
    pinned_materialization_for_read,
)


def refactor_candidates(
    project: str | None = None,
    refresh_id: str | None = None,
    min_similarity: float = 0.6,
) -> list[dict[str, Any]]:
    """Detect refactor candidates via symbol renaming patterns."""
    from difflib import SequenceMatcher

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_change import (
        load_added_deleted_symbol_pairs,
        load_renamed_symbols,
    )

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="mcp.change.refactor_candidates")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "symbol_change", caller="mcp.change.refactor_candidates")
            if refresh_id is None:
                return []

        # symbol_change.change_type is stored uppercase-word
        # ('ADDED'/'MODIFIED'/'DELETED'/'RENAMED') by the materializer in
        # analysis.code_index.symbol_changes — the single-letter labels in
        # the schema comment are misleading.
        renamed = load_renamed_symbols(conn, refresh_id=refresh_id, project=project)
        pairs = load_added_deleted_symbol_pairs(conn, refresh_id=refresh_id, project=project)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for proj, name, d, sha, _path in renamed:
        candidates.append({
            "project": proj,
            "old_name": name,
            "new_name": name,
            "similarity": 1.0,
            "date": _json_safe(d),
            "sha": sha[:8],
            "source": "explicit_rename",
        })

    for proj, old, new, d, sha in pairs:
        if old == new:
            continue
        key = (old, new)
        if key in seen:
            continue
        sim = SequenceMatcher(None, old, new).ratio()
        if sim < min_similarity:
            continue
        seen.add(key)
        candidates.append({
            "project": proj,
            "old_name": old,
            "new_name": new,
            "similarity": round(sim, 3),
            "date": _json_safe(d),
            "sha": sha[:8],
            "source": "similarity_match",
        })

    candidates.sort(key=lambda c: -c["similarity"])
    return candidates[:50]


def file_hotspots(
    top_n: int = 20,
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Churn hotspots by path root."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_change import load_file_churn_hotspots

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="mcp.change.file_hotspots")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "file_change_fact", caller="mcp.change.file_hotspots")
            if refresh_id is None:
                return []

        rows = load_file_churn_hotspots(
            conn, refresh_id=refresh_id, top_n=top_n, project=project
        )

    return [
        {"path_root": r[0], "commits": r[1], "file_changes": r[2], "project_count": r[3], "top_project": r[4]}
        for r in rows
    ]


@app.tool()
def code_history_claims(
    start: str,
    end: str,
    project: str | None = None,
    top_n: int = 25,
) -> list[dict[str, Any]]:
    """Observational git-history claims: hotspots, broad changes, and rework pressure."""
    from datetime import date

    from lynchpin.analysis.code_history_claims import (
        code_history_claims as _code_history_claims,
    )

    rows = _code_history_claims(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        project=project,
        top_n=min(max(top_n, 1), 200),
    )
    return [
        {
            "claim_id": row.claim_id,
            "claim_type": row.claim_type,
            "project": row.project,
            "date": _json_safe(row.date),
            "support_level": row.support_level,
            "confidence": row.confidence,
            "score": row.score,
            "summary": row.summary,
            "source_ids": list(row.source_ids),
            "relation_ids": list(row.relation_ids),
            "caveats": list(row.caveats),
            "payload": _json_safe(row.payload),
        }
        for row in rows
    ]


def conventional_commits(
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Conventional commit distribution per project."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_change import load_conventional_commit_distribution

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="mcp.change.conventional_commits")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="mcp.change.conventional_commits")
            if refresh_id is None:
                return []

        rows = load_conventional_commit_distribution(
            conn, refresh_id=refresh_id, project=project
        )

    return [{"project": r[0], "kind": r[1], "count": r[2], "pct": r[3]} for r in rows]


@app.tool()
def ai_tool_usage(
    start: str | None = None,
    end: str | None = None,
    project: str | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """AI tool usage patterns from polylogue action_events.

    Reads action_events table from the Polylogue archive (3.74M+ rows).
    Returns action_kind distribution with optional date and project filtering.
    """
    import sqlite3

    from lynchpin.core.config import get_config

    db_path = get_config().polylogue_db
    if not db_path.exists():
        return {
            "degraded": True,
            "reason": f"polylogue database not found at {db_path}",
            "rows": [],
        }

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Build SQL query with optional date range and project filter
        where_clauses = []
        params: list[Any] = []

        if start:
            where_clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            where_clauses.append("timestamp <= ?")
            params.append(end)

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

        sql = f"""
            SELECT action_kind, COUNT(*) AS n, COUNT(DISTINCT conversation_id) AS sessions
            FROM action_events
            WHERE {where_clause}
            GROUP BY action_kind
            ORDER BY n DESC
        """

        rows = conn.execute(sql, params).fetchall()
        result = [
            {"action_kind": r["action_kind"], "count": r["n"], "sessions": r["sessions"]}
            for r in rows
        ]

        conn.close()
        return {
            "degraded": False,
            "reason": None,
            "rows": result,
        }
    except Exception as exc:
        return {
            "degraded": True,
            "reason": f"failed to read polylogue action_events: {exc}",
            "rows": [],
        }


def breaking_changes(
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Breaking change tracker per project."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_change import load_breaking_change_commits

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="mcp.change.breaking_changes")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="mcp.change.breaking_changes")
            if refresh_id is None:
                return []

        rows = load_breaking_change_commits(conn, refresh_id=refresh_id, project=project)

    return [
        {"project": r[0], "sha": r[1][:8], "subject": r[2][:80], "date": _json_safe(r[3])}
        for r in rows
    ]


def commit_kind_attribution(
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Commit kind and AI attribution correlation.

    Returns a dict with:
      - "degraded": bool — True if ai_attribution is all NULL
      - "reason": str | None — explanation if degraded
      - "rows": list[dict] — the attribution data
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_change import (
        load_ai_attribution_count,
        load_commit_kind_ai_attribution,
    )

    materialization = (
        ensure_substrate_materialized_for_read(caller="mcp.change.commit_kind_attribution")
        if refresh_id is None
        else pinned_materialization_for_read(caller="mcp.change.commit_kind_attribution", refresh_id=refresh_id)
    )
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="mcp.change.commit_kind_attribution")
            if refresh_id is None:
                return {
                    "degraded": False,
                    "reason": None,
                    "materialization": materialization,
                    "rows": [],
                }

        # Check if any row has non-NULL ai_attribution
        ai_count = load_ai_attribution_count(conn, refresh_id=refresh_id)
        has_ai_data = ai_count > 0

        rows = load_commit_kind_ai_attribution(conn, refresh_id=refresh_id)

        degraded = not has_ai_data and len(rows) > 0
        reason = (
            f"ai_attribution backfill not run for refresh_id {refresh_id!r}"
            if degraded
            else None
        )

    return {
        "degraded": degraded,
        "reason": reason,
        "materialization": materialization,
        "rows": [{"kind": r[0], "total": r[1], "ai_assisted": r[2], "ai_pct": r[3]} for r in rows],
    }


def symbol_churn_hotspots(
    top_n: int = 20,
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Symbol churn hotspots by file path."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_change import load_symbol_churn_hotspots

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="mcp.change.symbol_churn_hotspots")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "symbol_change", caller="mcp.change.symbol_churn_hotspots")
            if refresh_id is None:
                return []

        rows = load_symbol_churn_hotspots(
            conn, refresh_id=refresh_id, top_n=top_n, project=project
        )

    return [{"path": r[0], "symbols": r[1], "commits": r[2], "changes": r[3], "projects": r[4]} for r in rows]


@app.tool()
def code_hotspots(
    view: str = "files",
    top_n: int = 20,
    project: str | None = None,
    refresh_id: str | None = None,
    min_similarity: float = 0.6,
) -> Any:
    """Code churn and refactor hotspots. view: files (churn hotspots by path root), symbols (symbol churn hotspots by file), refactors (rename/similarity-based refactor candidates)."""
    if view == "files":
        return file_hotspots(top_n=top_n, project=project, refresh_id=refresh_id)
    if view == "symbols":
        return symbol_churn_hotspots(top_n=top_n, project=project, refresh_id=refresh_id)
    if view == "refactors":
        return refactor_candidates(project=project, refresh_id=refresh_id, min_similarity=min_similarity)
    return {"error": f"unknown view {view!r}. choices: files, symbols, refactors"}


@app.tool()
def commit_analysis(
    view: str = "conventional",
    project: str | None = None,
    refresh_id: str | None = None,
) -> Any:
    """Commit-level analysis. view: conventional (commit type distribution), attribution (kind×AI attribution correlation), breaking (breaking-change commits)."""
    if view == "conventional":
        return conventional_commits(project=project, refresh_id=refresh_id)
    if view == "attribution":
        return commit_kind_attribution(refresh_id=refresh_id)
    if view == "breaking":
        return breaking_changes(project=project, refresh_id=refresh_id)
    return {"error": f"unknown view {view!r}. choices: conventional, attribution, breaking"}
