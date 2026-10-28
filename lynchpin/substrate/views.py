"""SQL views over the substrate that replace Python double-loop joins.

Each view materializes a relational join that lynchpin previously did in
Python with O(N×M) complexity. DuckDB's range joins + array intersection
make these sub-millisecond on realistic graph sizes.

Node ID format (must match evidence_graph.py constructors exactly):
- commit:        ``'git:' || project || ':' || sha``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb


VIEW_DEFINITIONS: dict[str, str] = {
    # ------------------------------------------------------------------
    # project_day_correlation  (Arc 2.4)
    #
    # Additive queryable surface over evidence_node.  Groups nodes by
    # (refresh_id, project, date), counting by kind and aggregating
    # payload extracts.  Does NOT replace work_correlation.py —
    # the Python composite layer remains canonical for full hydration,
    # per-kind classification, and status reasoning.
    #
    # JSON access:
    #   json_extract_string(payload, '$.field') → VARCHAR (NULL if absent)
    #   json_extract(payload, '$.field') → JSON (cast needed for arithmetic)
    # ------------------------------------------------------------------
    "project_day_correlation": """
        CREATE OR REPLACE VIEW project_day_correlation AS
        WITH correlation_nodes AS (
            SELECT refresh_id, id, kind, source, date, project, payload
            FROM evidence_node
            UNION ALL
            SELECT
                c.refresh_id,
                'git:' || c.project || ':' || c.sha AS id,
                'commit' AS kind,
                'git' AS source,
                CAST(c.authored_at AS DATE) AS date,
                c.project,
                json_object('commit', c.sha) AS payload
            FROM commit_fact c
            WHERE c.project IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM evidence_node n
                  WHERE n.refresh_id = c.refresh_id
                    AND n.kind = 'commit'
                    AND n.id = 'git:' || c.project || ':' || c.sha
              )
        )
        SELECT
            refresh_id,
            project,
            date,
            SUM(CASE WHEN kind = 'commit'         THEN 1 ELSE 0 END) AS commit_count,
            SUM(CASE WHEN kind IN ('github_issue','github_pr','github_ref') THEN 1 ELSE 0 END) AS github_item_count,
            SUM(CASE WHEN kind IN ('focus_day','focus_span') THEN 1 ELSE 0 END) AS focus_count,
            SUM(CASE WHEN kind = 'terminal_session' THEN 1 ELSE 0 END) AS terminal_count,
            SUM(CASE WHEN kind = 'raw_log'        THEN 1 ELSE 0 END) AS raw_log_count,
            ARRAY_AGG(DISTINCT json_extract_string(payload, '$.commit'))
                FILTER (WHERE kind = 'commit' AND payload IS NOT NULL) AS commit_shas,
            ARRAY_AGG(DISTINCT id) FILTER (WHERE kind IN ('github_issue','github_pr','github_ref')) AS github_node_ids,
            -- A focus_day rollup summarizes the same time as that day's
            -- focus_span details; prefer the rollup, never add both.
            COALESCE(
                SUM(CAST(json_extract(payload, '$.duration_s') AS DOUBLE))
                    FILTER (WHERE kind = 'focus_day' AND payload IS NOT NULL),
                SUM(CAST(json_extract(payload, '$.duration_s') AS DOUBLE))
                    FILTER (WHERE kind = 'focus_span' AND payload IS NOT NULL)
            ) AS focus_seconds,
            SUM(CAST(json_extract(payload, '$.duration_s') AS DOUBLE))
                FILTER (WHERE kind = 'terminal_session' AND payload IS NOT NULL) AS shell_seconds,
            COUNT(DISTINCT CASE
                WHEN kind = 'commit' THEN 'git'
                WHEN kind IN ('github_issue','github_pr','github_ref') THEN 'github'
                WHEN kind IN ('focus_day','focus_span') THEN 'activitywatch'
                WHEN kind = 'terminal_session' THEN 'terminal'
                WHEN kind = 'raw_log' THEN 'raw_log'
                ELSE NULL
            END) AS source_count
        FROM correlation_nodes
        WHERE project IS NOT NULL AND date IS NOT NULL
        GROUP BY refresh_id, project, date
    """,

    # ------------------------------------------------------------------
    # issue_closure_chain_walk  (Arc 2.5)
    #
    # Recursive CTE walking 'references' and 'mentions_project' edges from
    # github_issue anchor nodes.  Surfaces chain structure for downstream
    # classification by graph/issue_closure_chain.py.
    #
    # Depth cap at 5: covers issue → PR → commit → issue (4 hops) plus
    # headroom.  Intentional defensive measure against reference cycles —
    # the cycle itself is still visible in reachable_node_ids; only
    # infinite traversal is prevented.
    # ------------------------------------------------------------------
    "issue_closure_chain_walk": """
        CREATE OR REPLACE VIEW issue_closure_chain_walk AS
        WITH RECURSIVE refs AS (
            -- Anchor: every github_issue node is a chain root
            SELECT
                n.refresh_id,
                n.id                                                AS root_id,
                n.id                                                AS reachable_id,
                n.project                                           AS project,
                COALESCE(
                    json_extract_string(n.payload, '$.number'),
                    CAST(json_extract(n.payload, '$.number') AS VARCHAR)
                )                                                   AS issue_number,
                0                                                   AS depth
            FROM evidence_node n
            WHERE n.kind = 'github_issue'
               OR (n.kind = 'github_ref'
                   AND json_extract_string(n.payload, '$.kind') = 'issue')

            UNION ALL

            -- Follow references edges, capped at depth 5 (defensive against cycles)
            SELECT
                r.refresh_id,
                r.root_id,
                e.target_id        AS reachable_id,
                r.project,
                r.issue_number,
                r.depth + 1
            FROM refs r
            JOIN evidence_edge e
              ON e.refresh_id = r.refresh_id
             AND e.source_id  = r.reachable_id
             AND e.relation IN ('references', 'mentions_project')
            WHERE r.depth < 5
        )
        SELECT
            refresh_id,
            root_id,
            project,
            issue_number,
            ARRAY_AGG(DISTINCT reachable_id ORDER BY reachable_id) AS reachable_node_ids,
            MAX(depth)                                              AS chain_depth,
            COUNT(DISTINCT reachable_id)                            AS reachable_count
        FROM refs
        GROUP BY refresh_id, root_id, project, issue_number
    """,
}


def ensure_views(conn: "duckdb.DuckDBPyConnection") -> None:
    """Create all views idempotently (CREATE OR REPLACE)."""
    for ddl in VIEW_DEFINITIONS.values():
        conn.execute(ddl)
