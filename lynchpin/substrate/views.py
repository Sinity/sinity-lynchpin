"""SQL views over the substrate that replace Python double-loop joins.

Each view materializes a relational join that lynchpin previously did in
Python with O(N×M) complexity. DuckDB's range joins + array intersection
make these sub-millisecond on realistic graph sizes.

Node ID format (must match evidence_graph.py constructors exactly):
- ai_work_event: ``'polylogue:we:' || event_id || ':' || project``
- commit:        ``'git:' || project || ':' || sha``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb


VIEW_DEFINITIONS: dict[str, str] = {
    # ------------------------------------------------------------------
    # work_event_file_overlap
    #
    # Joins ai_work_event ↔ commit_fact on:
    #   - same project
    #   - commit authored_at within ±24h of work-event start_ts
    #   - non-empty intersection of file_paths and paths arrays
    #
    # shared_paths is the exact intersection; overlap_count its size.
    # The evidence string (truncation, "+N" suffix) is formatted in Python
    # from these columns — the view only materialises the raw data.
    # ------------------------------------------------------------------
    "work_event_file_overlap": """
        CREATE OR REPLACE VIEW work_event_file_overlap AS
        WITH filtered_paths AS (
            -- Exclude high-fanout paths that appear in most commits.
            -- These patterns match common files like __init__.py, Cargo.lock,
            -- lockfiles, etc. that provide no meaningful overlap signal.
            SELECT
                event_id,
                project,
                refresh_id,
                start_ts,
                list_filter(file_paths, x -> NOT (
                    x ILIKE '%__init__.py'
                    OR x ILIKE '%__pycache__%'
                    OR x LIKE '%Cargo.lock'
                    OR x LIKE '%pyproject.toml'
                    OR x LIKE '%package.json'
                    OR x LIKE '%package-lock.json'
                    OR x LIKE '%yarn.lock'
                    OR x LIKE '%pnpm-lock.yaml'
                    OR x LIKE '%flake.lock'
                    OR x LIKE '%lock.json'
                    OR x LIKE '%.gitignore'
                    OR x LIKE '%/.gitignore'
                    OR x LIKE 'node_modules/%'
                )) AS filtered_file_paths
            FROM ai_work_event
            WHERE start_ts IS NOT NULL
              AND project IS NOT NULL
              AND len(file_paths) > 0
        ),
        commit_paths_filtered AS (
            SELECT
                sha,
                project,
                refresh_id,
                authored_at,
                list_filter(paths, x -> NOT (
                    x ILIKE '%__init__.py'
                    OR x ILIKE '%__pycache__%'
                    OR x LIKE '%Cargo.lock'
                    OR x LIKE '%pyproject.toml'
                    OR x LIKE '%package.json'
                    OR x LIKE '%package-lock.json'
                    OR x LIKE '%yarn.lock'
                    OR x LIKE '%pnpm-lock.yaml'
                    OR x LIKE '%flake.lock'
                    OR x LIKE '%lock.json'
                    OR x LIKE '%.gitignore'
                    OR x LIKE '%/.gitignore'
                    OR x LIKE 'node_modules/%'
                )) AS filtered_paths
            FROM commit_fact
        )
        SELECT
            we.event_id,
            we.project,
            'polylogue:we:' || we.event_id || ':' || we.project  AS source_id,
            'git:' || c.project || ':' || c.sha                  AS target_id,
            list_intersect(we.filtered_file_paths, c.filtered_paths) AS shared_paths,
            len(list_intersect(we.filtered_file_paths, c.filtered_paths)) AS overlap_count,
            we.refresh_id   AS we_refresh_id,
            c.refresh_id    AS commit_refresh_id,
            c.sha           AS sha,
            c.authored_at   AS commit_at,
            we.start_ts     AS we_at
        FROM filtered_paths we
        JOIN commit_paths_filtered c
          ON we.project = c.project
         AND list_has_any(we.filtered_file_paths, c.filtered_paths)
         AND c.authored_at BETWEEN we.start_ts - INTERVAL 24 HOUR
                               AND we.start_ts + INTERVAL 24 HOUR
    """,

    # ------------------------------------------------------------------
    # work_event_symbol_overlap
    #
    # Joins ai_work_event ↔ commit_fact ↔ symbol_change on:
    #   - same project (we ↔ commit)
    #   - commit within ±24h of work-event start_ts
    #   - symbol_change.path suffix-matches any of we.file_paths
    #     (Python: ai.endswith(sym) or sym.endswith(ai), both lstripped of '/')
    #
    # UNNEST(we.file_paths) produces one row per ai_path per (we, commit, sym)
    # triple.  The WHERE filters for suffix match in either direction.
    # Final GROUP BY aggregates distinct symbol names per (we, commit) pair.
    # ------------------------------------------------------------------
    "work_event_symbol_overlap": """
        CREATE OR REPLACE VIEW work_event_symbol_overlap AS
        WITH joined AS (
            SELECT
                we.event_id,
                we.project,
                we.refresh_id   AS we_refresh_id,
                c.refresh_id    AS commit_refresh_id,
                c.sha,
                c.project       AS commit_project,
                we.start_ts     AS we_at,
                c.authored_at   AS commit_at,
                we.file_paths   AS we_files,
                sc.path         AS sym_path,
                sc.qualified_name AS qualified_name
            FROM ai_work_event we
            JOIN commit_fact c
              ON we.project = c.project
             AND c.authored_at BETWEEN we.start_ts - INTERVAL 24 HOUR
                                   AND we.start_ts + INTERVAL 24 HOUR
            JOIN symbol_change sc ON sc.sha = c.sha
            WHERE we.start_ts IS NOT NULL
              AND we.project IS NOT NULL
              AND len(we.file_paths) > 0
        ),
        path_matched AS (
            SELECT j.*
            FROM joined j,
                 UNNEST(j.we_files) AS t(ai_path)
            WHERE
                ends_with(ltrim(t.ai_path, '/'), ltrim(j.sym_path, '/'))
                OR ends_with(ltrim(j.sym_path, '/'), ltrim(t.ai_path, '/'))
        )
        SELECT
            event_id,
            project,
            we_refresh_id,
            commit_refresh_id,
            sha,
            'polylogue:we:' || event_id || ':' || project    AS source_id,
            'git:' || commit_project || ':' || sha           AS target_id,
            ARRAY_AGG(DISTINCT qualified_name)                AS shared_symbols,
            COUNT(DISTINCT qualified_name)                    AS symbol_count
        FROM path_matched
        WHERE qualified_name IS NOT NULL
          AND qualified_name <> ''
        GROUP BY event_id, project, we_refresh_id, commit_refresh_id, sha, source_id, target_id
    """,

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
            UNION ALL
            SELECT
                we.refresh_id,
                'polylogue:we:' || we.event_id || ':' || we.project AS id,
                'ai_work_event' AS kind,
                'polylogue' AS source,
                CAST(we.start_ts AS DATE) AS date,
                we.project,
                json_object(
                    'conversation_id', we.conversation_id,
                    'event_id', we.event_id
                ) AS payload
            FROM ai_work_event we
            WHERE we.project IS NOT NULL
              AND we.start_ts IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM evidence_node n
                  WHERE n.refresh_id = we.refresh_id
                    AND n.kind = 'ai_work_event'
                    AND n.project = we.project
                    AND json_extract_string(n.payload, '$.event_id') = we.event_id
              )
        )
        SELECT
            refresh_id,
            project,
            date,
            SUM(CASE WHEN kind = 'commit'         THEN 1 ELSE 0 END) AS commit_count,
            SUM(CASE WHEN kind = 'ai_session'     THEN 1 ELSE 0 END) AS ai_session_count,
            SUM(CASE WHEN kind = 'ai_work_event'  THEN 1 ELSE 0 END) AS ai_work_event_count,
            SUM(CASE WHEN kind IN ('github_issue','github_pr','github_ref') THEN 1 ELSE 0 END) AS github_item_count,
            SUM(CASE WHEN kind IN ('focus_day','focus_span') THEN 1 ELSE 0 END) AS focus_count,
            SUM(CASE WHEN kind = 'terminal_session' THEN 1 ELSE 0 END) AS terminal_count,
            SUM(CASE WHEN kind = 'raw_log'        THEN 1 ELSE 0 END) AS raw_log_count,
            ARRAY_AGG(DISTINCT json_extract_string(payload, '$.commit'))
                FILTER (WHERE kind = 'commit' AND payload IS NOT NULL) AS commit_shas,
            ARRAY_AGG(DISTINCT json_extract_string(payload, '$.conversation_id'))
                FILTER (WHERE kind IN ('ai_session','ai_work_event') AND payload IS NOT NULL) AS conversation_ids,
            ARRAY_AGG(DISTINCT id) FILTER (WHERE kind IN ('github_issue','github_pr','github_ref')) AS github_node_ids,
            SUM(CAST(json_extract(payload, '$.duration_s') AS DOUBLE))
                FILTER (WHERE kind IN ('focus_day','focus_span') AND payload IS NOT NULL) AS focus_seconds,
            SUM(CAST(json_extract(payload, '$.duration_s') AS DOUBLE))
                FILTER (WHERE kind = 'terminal_session' AND payload IS NOT NULL) AS shell_seconds,
            COUNT(DISTINCT CASE
                WHEN kind = 'commit' THEN 'git'
                WHEN kind IN ('ai_session','ai_work_event') THEN 'polylogue'
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
