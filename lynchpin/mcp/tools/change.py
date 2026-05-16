"""Code-change and churn MCP tools.

Do not enable postponed annotations in this module: FastMCP inspects function
annotations while registering @app.tool functions.
"""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id, json_safe as _json_safe


@app.tool()
def refactor_candidates(
    project: str | None = None,
    refresh_id: str | None = None,
    min_similarity: float = 0.6,
) -> list[dict[str, Any]]:
    """Detect refactor candidates via symbol renaming patterns."""
    from difflib import SequenceMatcher

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "symbol_change")
            if refresh_id is None:
                return []

        proj_filter = "AND project = ?" if project else ""
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)

        renamed = conn.execute(f"""
            SELECT project, qualified_name, date, sha, path
            FROM symbol_change
            WHERE refresh_id = ? AND change_type = 'R' {proj_filter}
            ORDER BY date
        """, params).fetchall()

        pairs = conn.execute(f"""
            WITH added AS (
                SELECT project, qualified_name, date, sha
                FROM symbol_change
                WHERE refresh_id = ? AND change_type = 'A' {proj_filter}
            ),
            deleted AS (
                SELECT project, qualified_name, date, sha
                FROM symbol_change
                WHERE refresh_id = ? AND change_type = 'D' {proj_filter}
            )
            SELECT a.project, d.qualified_name AS old_name,
                   a.qualified_name AS new_name,
                   a.date, a.sha
            FROM added a
            JOIN deleted d ON a.project = d.project
            WHERE a.date >= d.date
            ORDER BY a.date
            LIMIT 500
        """, [refresh_id] + ([project] if project else []) + [refresh_id] + ([project] if project else [])).fetchall()

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


@app.tool()
def file_hotspots(
    top_n: int = 20,
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Churn hotspots by path root."""
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "file_change_fact")
            if refresh_id is None:
                return []

        proj_filter = "AND project = ?" if project else ""
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        params.append(int(top_n))

        rows = conn.execute(f"""
            SELECT path_root,
                   COUNT(DISTINCT sha) AS commits,
                   COUNT(*) AS file_changes,
                   COUNT(DISTINCT project) AS project_count,
                   MODE(project) AS top_project
            FROM file_change_fact
            WHERE refresh_id = ? AND path_root IS NOT NULL
              AND path_root != '' {proj_filter}
            GROUP BY path_root
            ORDER BY commits DESC
            LIMIT ?
        """, params).fetchall()

    return [
        {"path_root": r[0], "commits": r[1], "file_changes": r[2], "project_count": r[3], "top_project": r[4]}
        for r in rows
    ]


@app.tool()
def conventional_commits(
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Conventional commit distribution per project."""
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "commit_fact")
            if refresh_id is None:
                return []

        proj_filter = "AND project = ?" if project else ""
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)

        rows = conn.execute(f"""
            SELECT project, conventional_kind, COUNT(*) AS cnt,
                   ROUND(COUNT(*)*100.0/SUM(COUNT(*)) OVER(PARTITION BY project), 1) AS pct
            FROM commit_fact
            WHERE refresh_id = ? AND conventional_kind IS NOT NULL {proj_filter}
            GROUP BY project, conventional_kind
            ORDER BY project, cnt DESC
        """, params).fetchall()

    return [{"project": r[0], "kind": r[1], "count": r[2], "pct": r[3]} for r in rows]


@app.tool()
def ai_tool_usage(
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """AI tool usage patterns from work events."""
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "ai_work_event")
            if refresh_id is None:
                return []

        proj_filter = "AND project = ?" if project else ""
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)

        rows = conn.execute(f"""
            WITH unnested AS (
                SELECT project, UNNEST(tools_used) AS tool
                FROM ai_work_event
                WHERE refresh_id = ? AND len(tools_used) > 0 {proj_filter}
            )
            SELECT project, tool, COUNT(*) AS cnt
            FROM unnested GROUP BY project, tool ORDER BY project, cnt DESC
        """, params).fetchall()

    return [{"project": r[0], "tool": r[1], "count": r[2]} for r in rows]


@app.tool()
def breaking_changes(
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Breaking change tracker per project."""
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "commit_fact")
            if refresh_id is None:
                return []

        proj_filter = "AND project = ?" if project else ""
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)

        rows = conn.execute(f"""
            SELECT project, sha, subject, authored_at
            FROM commit_fact
            WHERE refresh_id = ? AND breaking_change = TRUE {proj_filter}
            ORDER BY authored_at DESC
        """, params).fetchall()

    return [
        {"project": r[0], "sha": r[1][:8], "subject": r[2][:80], "date": _json_safe(r[3])}
        for r in rows
    ]


@app.tool()
def commit_kind_attribution(
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Commit kind and AI attribution correlation."""
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "commit_fact")
            if refresh_id is None:
                return []

        rows = conn.execute("""
            SELECT conventional_kind, COUNT(*) AS total,
                   SUM(CASE WHEN ai_attribution IS NOT NULL THEN 1 ELSE 0 END) AS ai_assisted,
                   ROUND(SUM(CASE WHEN ai_attribution IS NOT NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),1) AS ai_pct
            FROM commit_fact
            WHERE refresh_id = ? AND conventional_kind IS NOT NULL
            GROUP BY conventional_kind
            ORDER BY total DESC
        """, [refresh_id]).fetchall()

    return [{"kind": r[0], "total": r[1], "ai_assisted": r[2], "ai_pct": r[3]} for r in rows]


@app.tool()
def symbol_churn_hotspots(
    top_n: int = 20,
    project: str | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Symbol churn hotspots by file path."""
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, "symbol_change")
            if refresh_id is None:
                return []

        proj_filter = "AND project = ?" if project else ""
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        params.append(int(top_n))

        rows = conn.execute(f"""
            SELECT path,
                   COUNT(DISTINCT qualified_name) AS symbols,
                   COUNT(DISTINCT sha) AS commits,
                   COUNT(*) AS changes,
                   COUNT(DISTINCT project) AS projects
            FROM symbol_change
            WHERE refresh_id = ? AND path IS NOT NULL AND path != ''
              {proj_filter}
            GROUP BY path
            ORDER BY symbols DESC
            LIMIT ?
        """, params).fetchall()

    return [{"path": r[0], "symbols": r[1], "commits": r[2], "changes": r[3], "projects": r[4]} for r in rows]
