"""DuckDB substrate schema (DDL).

Phase 2.1 tables (this file):
- ``commit_fact``      — git commits (typed shape from GitCommitFact + JSON-layer annotations consumed downstream)
- ``file_change_fact`` — per-file-per-commit churn
- ``ai_work_event``    — Polylogue work events with Lynchpin overlay kind/tier
- ``symbol_change``    — commit × symbol change rows (from active_symbol_changes)
- ``pr_review_row``    — PR review topology (from M.7)

Phase 2.2 tables (added later):
- ``evidence_node``, ``evidence_edge``, ``analysis_claim``

Schema versioning: ``substrate_meta(key='version', value=str(SUBSTRATE_VERSION))``.
On version mismatch ``apply_schema`` drops + re-creates. The substrate is
*derived*, so re-promote is the migration story — not in-place ALTER.

Conventions:
- Every domain table has ``refresh_id VARCHAR NOT NULL`` for idempotent
  re-promotion (DELETE WHERE refresh_id = ? then INSERT).
- ``materialized_at TIMESTAMP`` defaults to CURRENT_TIMESTAMP for audit.
- Datetimes are stored as ``TIMESTAMPTZ`` (DuckDB normalizes to UTC).
- String lists use ``VARCHAR[]``; structured small dicts use ``STRUCT(...)``;
  free-form payloads use ``JSON``.
"""

from __future__ import annotations

DROP_STATEMENTS: tuple[str, ...] = (
    "DROP TABLE IF EXISTS substrate_source_status",
    "DROP TABLE IF EXISTS evidence_edge",
    "DROP TABLE IF EXISTS evidence_node",
    "DROP TABLE IF EXISTS evidence_graph_build",
    "DROP TABLE IF EXISTS pr_review_row",
    "DROP TABLE IF EXISTS symbol_change",
    "DROP TABLE IF EXISTS ai_work_event",
    "DROP TABLE IF EXISTS file_change_fact",
    "DROP TABLE IF EXISTS commit_fact",
)


DDL_STATEMENTS: tuple[str, ...] = (
    # ────────────────────────────────────────────────────────────────────
    # commit_fact
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE commit_fact (
        sha                     VARCHAR NOT NULL,
        repo                    VARCHAR NOT NULL,
        project                 VARCHAR,
        authored_at             TIMESTAMPTZ NOT NULL,
        author                  VARCHAR,
        subject                 VARCHAR,
        lines_added             INTEGER NOT NULL DEFAULT 0,
        lines_deleted           INTEGER NOT NULL DEFAULT 0,
        lines_changed           INTEGER NOT NULL DEFAULT 0,
        files_changed           INTEGER NOT NULL DEFAULT 0,
        paths                   VARCHAR[] NOT NULL DEFAULT [],
        path_roots              VARCHAR[] NOT NULL DEFAULT [],
        -- JSON-layer annotations consumed by composite/causal_chains.py and issue_closure_chain.py
        conventional_kind       VARCHAR,
        conventional_scope      VARCHAR,
        conventional_signature  VARCHAR,
        breaking_change         BOOLEAN NOT NULL DEFAULT FALSE,
        github_refs             STRUCT(issues INTEGER[], prs INTEGER[]),
        ai_attribution          JSON,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        -- refresh_id in PK preserves multi-snapshot history (D.1). Without it,
        -- promoting commits at refresh_A then refresh_B PK-collides because
        -- DELETE WHERE refresh_id only clears one partition.
        PRIMARY KEY (sha, repo, refresh_id)
    )
    """,
    "CREATE INDEX commit_fact_project_authored_at ON commit_fact(project, authored_at)",
    "CREATE INDEX commit_fact_authored_at ON commit_fact(authored_at)",
    "CREATE INDEX commit_fact_refresh_id ON commit_fact(refresh_id)",

    # ────────────────────────────────────────────────────────────────────
    # file_change_fact
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE file_change_fact (
        sha             VARCHAR NOT NULL,
        repo            VARCHAR NOT NULL,
        project         VARCHAR,
        authored_at     TIMESTAMPTZ NOT NULL,
        path            VARCHAR NOT NULL,
        path_root       VARCHAR,
        lines_added     INTEGER NOT NULL DEFAULT 0,
        lines_deleted   INTEGER NOT NULL DEFAULT 0,
        lines_changed   INTEGER NOT NULL DEFAULT 0,
        change_type     VARCHAR,                    -- modified | added | deleted | renamed
        previous_path   VARCHAR,                    -- non-NULL only for renames
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (sha, repo, path, refresh_id)
    )
    """,
    "CREATE INDEX file_change_fact_path ON file_change_fact(path)",
    "CREATE INDEX file_change_fact_project_authored_at ON file_change_fact(project, authored_at)",
    "CREATE INDEX file_change_fact_refresh_id ON file_change_fact(refresh_id)",

    # ────────────────────────────────────────────────────────────────────
    # ai_work_event
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE ai_work_event (
        event_id               VARCHAR NOT NULL,
        conversation_id        VARCHAR NOT NULL,
        provider               VARCHAR NOT NULL,
        project                VARCHAR,
        kind                   VARCHAR NOT NULL,
        kind_confidence        DOUBLE NOT NULL DEFAULT 0.0,
        kind_tier              VARCHAR,             -- high | medium | low (Arc K)
        kind_source            VARCHAR,             -- polylogue | lynchpin_overlay | agreement | disagreement
        polylogue_kind         VARCHAR,
        polylogue_confidence   DOUBLE,
        overlay_kind           VARCHAR,
        overlay_confidence     DOUBLE,
        file_paths             VARCHAR[] NOT NULL DEFAULT [],
        tools_used             VARCHAR[] NOT NULL DEFAULT [],
        start_ts               TIMESTAMPTZ,         -- nullable: see WorkEvent docs
        end_ts                 TIMESTAMPTZ,
        duration_ms            BIGINT NOT NULL DEFAULT 0,  -- authoritative duration column
        summary                VARCHAR,
        refresh_id             VARCHAR NOT NULL,
        materialized_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (event_id, refresh_id)
    )
    """,
    "CREATE INDEX ai_work_event_project_start ON ai_work_event(project, start_ts)",
    "CREATE INDEX ai_work_event_conversation ON ai_work_event(conversation_id)",
    "CREATE INDEX ai_work_event_kind_tier ON ai_work_event(kind, kind_tier)",
    "CREATE INDEX ai_work_event_refresh_id ON ai_work_event(refresh_id)",

    # ────────────────────────────────────────────────────────────────────
    # symbol_change
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE symbol_change (
        sha                  VARCHAR NOT NULL,
        project              VARCHAR NOT NULL,
        date                 DATE NOT NULL,
        path                 VARCHAR NOT NULL,
        change_type          VARCHAR NOT NULL,     -- M | A | D | R
        qualified_name       VARCHAR NOT NULL,
        symbol_kind          VARCHAR NOT NULL,
        exported             BOOLEAN NOT NULL DEFAULT FALSE,
        breaking_candidate   BOOLEAN NOT NULL DEFAULT FALSE,
        refresh_id           VARCHAR NOT NULL,
        materialized_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (sha, path, qualified_name, refresh_id)
    )
    """,
    "CREATE INDEX symbol_change_project_date ON symbol_change(project, date)",
    "CREATE INDEX symbol_change_path ON symbol_change(path)",
    "CREATE INDEX symbol_change_qualified_name ON symbol_change(qualified_name)",
    "CREATE INDEX symbol_change_refresh_id ON symbol_change(refresh_id)",

    # ────────────────────────────────────────────────────────────────────
    # pr_review_row
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE pr_review_row (
        project                       VARCHAR NOT NULL,
        number                        INTEGER NOT NULL,
        title                         VARCHAR,
        state                         VARCHAR,
        url                           VARCHAR,
        author                        VARCHAR,
        created_at                    TIMESTAMPTZ,
        closed_at                     TIMESTAMPTZ,
        merged_at                     TIMESTAMPTZ,
        review_count                  INTEGER NOT NULL DEFAULT 0,
        review_decisions              VARCHAR[] NOT NULL DEFAULT [],
        review_round_count            INTEGER NOT NULL DEFAULT 0,
        reviewer_count                INTEGER NOT NULL DEFAULT 0,
        reviewers                     VARCHAR[] NOT NULL DEFAULT [],
        review_comment_count          INTEGER NOT NULL DEFAULT 0,
        top_level_comment_count       INTEGER NOT NULL DEFAULT 0,
        changes_requested_count       INTEGER NOT NULL DEFAULT 0,
        approval_count                INTEGER NOT NULL DEFAULT 0,
        dismissed_count               INTEGER NOT NULL DEFAULT 0,
        time_to_first_review_minutes  DOUBLE,
        time_to_close_minutes         DOUBLE,
        time_to_merge_minutes         DOUBLE,
        final_decision                VARCHAR,
        friction_signals              VARCHAR[] NOT NULL DEFAULT [],
        refresh_id                    VARCHAR NOT NULL,
        materialized_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, number, refresh_id)
    )
    """,
    "CREATE INDEX pr_review_row_state ON pr_review_row(state)",
    "CREATE INDEX pr_review_row_merged_at ON pr_review_row(merged_at)",
    "CREATE INDEX pr_review_row_refresh_id ON pr_review_row(refresh_id)",
)


DDL_STATEMENTS = (*DDL_STATEMENTS, *(
    # ────────────────────────────────────────────────────────────────────
    # evidence_graph_build — one row per build, anchors nodes/edges
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE evidence_graph_build (
        refresh_id      VARCHAR PRIMARY KEY,
        start_date      DATE NOT NULL,
        end_date        DATE NOT NULL,
        mode            VARCHAR NOT NULL,        -- local-fast | local-heavy | network
        projects        VARCHAR[] NOT NULL DEFAULT [],   -- empty = all projects
        node_count      INTEGER NOT NULL DEFAULT 0,
        edge_count      INTEGER NOT NULL DEFAULT 0,
        caveats         JSON NOT NULL DEFAULT '[]',  -- list[{source, status, message}]
        generated_at    TIMESTAMPTZ NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX evidence_graph_build_window ON evidence_graph_build(start_date, end_date, mode)",

    # ────────────────────────────────────────────────────────────────────
    # evidence_node — typed nodes from the evidence graph
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE evidence_node (
        refresh_id      VARCHAR NOT NULL,
        id              VARCHAR NOT NULL,
        kind            VARCHAR NOT NULL,
        source          VARCHAR NOT NULL,
        date            DATE NOT NULL,
        project         VARCHAR,
        summary         VARCHAR NOT NULL,
        start_ts        TIMESTAMPTZ,
        end_ts          TIMESTAMPTZ,
        url             VARCHAR,
        payload         JSON,
        provenance      STRUCT(
            source       VARCHAR,
            cost         VARCHAR,
            path         VARCHAR,
            generated_at TIMESTAMPTZ,
            note         VARCHAR
        ),
        caveats         JSON NOT NULL DEFAULT '[]',  -- list[{source, status, message}]
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (refresh_id, id)
    )
    """,
    "CREATE INDEX evidence_node_kind ON evidence_node(kind)",
    "CREATE INDEX evidence_node_project_date ON evidence_node(project, date)",
    "CREATE INDEX evidence_node_temporal ON evidence_node(start_ts, end_ts)",

    # ────────────────────────────────────────────────────────────────────
    # evidence_edge — typed edges
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE evidence_edge (
        refresh_id      VARCHAR NOT NULL,
        source_id       VARCHAR NOT NULL,
        target_id       VARCHAR NOT NULL,
        relation        VARCHAR NOT NULL,
        evidence        VARCHAR NOT NULL,
        weight          DOUBLE NOT NULL DEFAULT 1.0,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (refresh_id, source_id, target_id, relation)
    )
    """,
    "CREATE INDEX evidence_edge_relation ON evidence_edge(relation)",
    "CREATE INDEX evidence_edge_source ON evidence_edge(source_id)",
    "CREATE INDEX evidence_edge_target ON evidence_edge(target_id)",

    # ────────────────────────────────────────────────────────────────────
    # substrate_source_status — per-source readiness for each promote run
    # ────────────────────────────────────────────────────────────────────
    # Records the outcome of every promoter call: ok | empty | unavailable | error.
    # Distinguishes "source had nothing in window" (empty, normal) from "source
    # is broken" (unavailable / error). Lets MCP / readiness reports tell the
    # difference between an empty substrate and a silently failed one — fixes
    # the prior failure mode where polylogue-stale → ai_work_event=0 looked
    # identical to a successful promote with no events.
    """
    CREATE TABLE substrate_source_status (
        refresh_id      VARCHAR NOT NULL,
        source          VARCHAR NOT NULL,
        status          VARCHAR NOT NULL,
        reason          VARCHAR,
        row_count       INTEGER NOT NULL DEFAULT 0,
        window_start    DATE,
        window_end      DATE,
        recorded_at     TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (refresh_id, source)
    )
    """,
    "CREATE INDEX substrate_source_status_source ON substrate_source_status(source)",
    "CREATE INDEX substrate_source_status_status ON substrate_source_status(status)",
))


# Domain table names — used by writers/readers/tests that iterate uniformly.
DOMAIN_TABLES: tuple[str, ...] = (
    "commit_fact",
    "file_change_fact",
    "ai_work_event",
    "symbol_change",
    "pr_review_row",
    "evidence_graph_build",
    "evidence_node",
    "evidence_edge",
    "substrate_source_status",
)
