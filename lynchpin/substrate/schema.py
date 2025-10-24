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
    "DROP TABLE IF EXISTS github_pr_review_comment",
    "DROP TABLE IF EXISTS github_pr_review",
    "DROP TABLE IF EXISTS github_pr_comment",
    "DROP TABLE IF EXISTS github_pr",
    "DROP TABLE IF EXISTS github_issue_comment",
    "DROP TABLE IF EXISTS github_issue",
    "DROP TABLE IF EXISTS code_snapshot_slice",
    "DROP TABLE IF EXISTS code_snapshot_run",
    "DROP TABLE IF EXISTS analysis_claim",
    "DROP TABLE IF EXISTS substrate_run_step",
    "DROP TABLE IF EXISTS substrate_promotion_run",
    "DROP TABLE IF EXISTS borg_drill_run",
    "DROP TABLE IF EXISTS sinnix_generation",
    "DROP TABLE IF EXISTS machine_experiment_run",
    "DROP TABLE IF EXISTS work_observation_test_result",
    "DROP TABLE IF EXISTS work_observation_stage",
    "DROP TABLE IF EXISTS work_observation",
    "DROP TABLE IF EXISTS machine_network_sample",
    "DROP TABLE IF EXISTS machine_kill_event",
    "DROP TABLE IF EXISTS machine_cgroup_memory_sample",
    "DROP TABLE IF EXISTS machine_process_memory_sample",
    "DROP TABLE IF EXISTS machine_process_io_delta_sample",
    "DROP TABLE IF EXISTS machine_service_state",
    "DROP TABLE IF EXISTS machine_gpu_sample",
    "DROP TABLE IF EXISTS machine_metric_sample",
    "DROP TABLE IF EXISTS activity_title_usage",
    "DROP TABLE IF EXISTS activity_content_bucket",
    "DROP TABLE IF EXISTS activity_content_day",
    "DROP TABLE IF EXISTS title_classification",
    "DROP TABLE IF EXISTS operator_day",
    "DROP TABLE IF EXISTS personal_daily_signal",
    "DROP TABLE IF EXISTS spotify_daily",
    "DROP TABLE IF EXISTS substrate_source_status",
    "DROP TABLE IF EXISTS evidence_edge",
    "DROP TABLE IF EXISTS evidence_node",
    "DROP TABLE IF EXISTS evidence_graph_build",
    "DROP TABLE IF EXISTS pr_review_row",
    "DROP TABLE IF EXISTS symbol_change",
    "DROP TABLE IF EXISTS polylogue_cross_source_overlap",
    "DROP TABLE IF EXISTS polylogue_timeline_span",
    "DROP TABLE IF EXISTS polylogue_session_time_composition",
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
        -- JSON-layer annotations consumed by graph/causal_chains.py and issue_closure_chain.py
        conventional_kind       VARCHAR,
        conventional_scope      VARCHAR,
        conventional_signature  VARCHAR,
        breaking_change         BOOLEAN NOT NULL DEFAULT FALSE,
        github_refs             STRUCT(issues INTEGER[], prs INTEGER[]),
        ai_attribution          JSON,
        -- Active-facts enrichment columns (Arc 3: make substrate the canonical source,
        -- replacing active_commit_facts.json for all downstream consumers).
        categories              JSON NOT NULL DEFAULT '{}',
        change_types            JSON NOT NULL DEFAULT '{}',
        classified_files_changed INTEGER NOT NULL DEFAULT 0,
        parent_count            INTEGER NOT NULL DEFAULT 1,
        default_branch          VARCHAR,
        head                    VARCHAR,
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
        change_type     VARCHAR,                    -- lowercase: 'modified' | 'added' | 'deleted' | 'renamed' | 'type_changed' | 'copied'
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
        kind_source            VARCHAR,             -- source | lynchpin_overlay | agreement | disagreement
        source_kind            VARCHAR,
        source_confidence      DOUBLE,
        overlay_kind           VARCHAR,
        overlay_confidence     DOUBLE,
        workflow_shape         VARCHAR,
        workflow_shape_confidence DOUBLE NOT NULL DEFAULT 0.0,
        terminal_state         VARCHAR,
        terminal_state_confidence DOUBLE NOT NULL DEFAULT 0.0,
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
    # Polylogue session time-composition — AW-like session timelines,
    # per-session rollups, and cross-source overlaps.
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE polylogue_session_time_composition (
        session_id              VARCHAR NOT NULL,
        provider                VARCHAR NOT NULL,
        title                   VARCHAR,
        start_ts                TIMESTAMPTZ,
        end_ts                  TIMESTAMPTZ,
        status                  VARCHAR NOT NULL,
        reason                  VARCHAR,
        message_count           INTEGER NOT NULL DEFAULT 0,
        wall_seconds            DOUBLE NOT NULL DEFAULT 0.0,
        engaged_seconds         DOUBLE NOT NULL DEFAULT 0.0,
        span_count              INTEGER NOT NULL DEFAULT 0,
        overlap_count           INTEGER NOT NULL DEFAULT 0,
        seconds_by_lane         JSON NOT NULL DEFAULT '{}',
        seconds_by_kind         JSON NOT NULL DEFAULT '{}',
        cross_source_seconds    JSON NOT NULL DEFAULT '{}',
        projects                VARCHAR[] NOT NULL DEFAULT [],
        tags                    VARCHAR[] NOT NULL DEFAULT [],
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (session_id, refresh_id)
    )
    """,
    "CREATE INDEX polylogue_session_time_start ON polylogue_session_time_composition(start_ts)",
    "CREATE INDEX polylogue_session_time_status ON polylogue_session_time_composition(status)",
    "CREATE INDEX polylogue_session_time_refresh_id ON polylogue_session_time_composition(refresh_id)",
    """
    CREATE TABLE polylogue_timeline_span (
        span_id                 VARCHAR NOT NULL,
        session_id              VARCHAR NOT NULL,
        provider                VARCHAR NOT NULL,
        lane                    VARCHAR NOT NULL,
        kind                    VARCHAR NOT NULL,
        start_ts                TIMESTAMPTZ NOT NULL,
        end_ts                  TIMESTAMPTZ NOT NULL,
        duration_s              DOUBLE NOT NULL DEFAULT 0.0,
        source                  VARCHAR NOT NULL,
        role                    VARCHAR,
        project                 VARCHAR,
        app                     VARCHAR,
        summary                 VARCHAR,
        tool_names              VARCHAR[] NOT NULL DEFAULT [],
        fidelity                VARCHAR NOT NULL,
        confidence              DOUBLE NOT NULL DEFAULT 1.0,
        metadata                JSON NOT NULL DEFAULT '{}',
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (span_id, refresh_id)
    )
    """,
    "CREATE INDEX polylogue_timeline_session ON polylogue_timeline_span(session_id)",
    "CREATE INDEX polylogue_timeline_start ON polylogue_timeline_span(start_ts)",
    "CREATE INDEX polylogue_timeline_lane_kind ON polylogue_timeline_span(lane, kind)",
    "CREATE INDEX polylogue_timeline_refresh_id ON polylogue_timeline_span(refresh_id)",
    """
    CREATE TABLE polylogue_cross_source_overlap (
        session_id              VARCHAR NOT NULL,
        primary_span_id         VARCHAR NOT NULL,
        other_span_id           VARCHAR NOT NULL,
        source                  VARCHAR NOT NULL,
        lane                    VARCHAR NOT NULL,
        kind                    VARCHAR NOT NULL,
        start_ts                TIMESTAMPTZ NOT NULL,
        end_ts                  TIMESTAMPTZ NOT NULL,
        duration_s              DOUBLE NOT NULL DEFAULT 0.0,
        project                 VARCHAR,
        metadata                JSON NOT NULL DEFAULT '{}',
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (primary_span_id, other_span_id, refresh_id)
    )
    """,
    "CREATE INDEX polylogue_overlap_session ON polylogue_cross_source_overlap(session_id)",
    "CREATE INDEX polylogue_overlap_source ON polylogue_cross_source_overlap(source)",
    "CREATE INDEX polylogue_overlap_refresh_id ON polylogue_cross_source_overlap(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # symbol_change
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE symbol_change (
        sha                  VARCHAR NOT NULL,
        project              VARCHAR NOT NULL,
        date                 DATE NOT NULL,
        path                 VARCHAR NOT NULL,
        change_type          VARCHAR NOT NULL,     -- UPPERCASE: 'ADDED' | 'MODIFIED' | 'DELETED' | 'RENAMED' (writer uppercases the git-status word, NOT the single-letter code)
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
    # ────────────────────────────────────────────────────────────────────
    # github_issue — normalized issue metadata + body from github_context
    # ────────────────────────────────────────────────────────────────────
    # refresh_id='latest' for overwrite semantics (same as code_snapshot_*).
    """
    CREATE TABLE github_issue (
        project         VARCHAR NOT NULL,
        number          INTEGER NOT NULL,
        title           VARCHAR,
        body            VARCHAR,
        state           VARCHAR,
        author          VARCHAR,
        labels          VARCHAR[] NOT NULL DEFAULT [],
        comment_count   INTEGER NOT NULL DEFAULT 0,
        created_at      TIMESTAMPTZ,
        updated_at      TIMESTAMPTZ,
        closed_at       TIMESTAMPTZ,
        url             VARCHAR,
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, number, refresh_id)
    )
    """,
    "CREATE INDEX github_issue_state ON github_issue(state)",
    "CREATE INDEX github_issue_project ON github_issue(project)",
    "CREATE INDEX github_issue_refresh_id ON github_issue(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # github_issue_comment — one row per comment on an issue
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE github_issue_comment (
        project         VARCHAR NOT NULL,
        issue_number    INTEGER NOT NULL,
        comment_idx     INTEGER NOT NULL,
        author          VARCHAR,
        body            VARCHAR,
        created_at      TIMESTAMPTZ,
        url             VARCHAR,
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, issue_number, comment_idx, refresh_id)
    )
    """,
    "CREATE INDEX github_issue_comment_issue ON github_issue_comment(project, issue_number)",
    "CREATE INDEX github_issue_comment_refresh_id ON github_issue_comment(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # github_pr — normalized PR metadata + body; merge_commit joins to commit_fact
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE github_pr (
        project              VARCHAR NOT NULL,
        number               INTEGER NOT NULL,
        title                VARCHAR,
        body                 VARCHAR,
        state                VARCHAR,
        author               VARCHAR,
        labels               VARCHAR[] NOT NULL DEFAULT [],
        merge_commit         VARCHAR,
        review_decision      VARCHAR,
        comment_count        INTEGER NOT NULL DEFAULT 0,
        review_count         INTEGER NOT NULL DEFAULT 0,
        review_comment_count INTEGER NOT NULL DEFAULT 0,
        created_at           TIMESTAMPTZ,
        updated_at           TIMESTAMPTZ,
        closed_at            TIMESTAMPTZ,
        merged_at            TIMESTAMPTZ,
        url                  VARCHAR,
        refresh_id           VARCHAR NOT NULL,
        materialized_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, number, refresh_id)
    )
    """,
    "CREATE INDEX github_pr_state ON github_pr(state)",
    "CREATE INDEX github_pr_merge_commit ON github_pr(merge_commit)",
    "CREATE INDEX github_pr_merged_at ON github_pr(merged_at)",
    "CREATE INDEX github_pr_project ON github_pr(project)",
    "CREATE INDEX github_pr_refresh_id ON github_pr(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # github_pr_comment — top-level PR discussion comments
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE github_pr_comment (
        project         VARCHAR NOT NULL,
        pr_number       INTEGER NOT NULL,
        comment_idx     INTEGER NOT NULL,
        author          VARCHAR,
        body            VARCHAR,
        created_at      TIMESTAMPTZ,
        url             VARCHAR,
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, pr_number, comment_idx, refresh_id)
    )
    """,
    "CREATE INDEX github_pr_comment_pr ON github_pr_comment(project, pr_number)",
    "CREATE INDEX github_pr_comment_refresh_id ON github_pr_comment(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # github_pr_review — review submissions (APPROVED, CHANGES_REQUESTED, etc.)
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE github_pr_review (
        project         VARCHAR NOT NULL,
        pr_number       INTEGER NOT NULL,
        review_idx      INTEGER NOT NULL,
        author          VARCHAR,
        state           VARCHAR,
        body            VARCHAR,
        submitted_at    TIMESTAMPTZ,
        url             VARCHAR,
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, pr_number, review_idx, refresh_id)
    )
    """,
    "CREATE INDEX github_pr_review_state ON github_pr_review(state)",
    "CREATE INDEX github_pr_review_pr ON github_pr_review(project, pr_number)",
    "CREATE INDEX github_pr_review_refresh_id ON github_pr_review(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # github_pr_review_comment — inline code review comments (diff-level)
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE github_pr_review_comment (
        project         VARCHAR NOT NULL,
        pr_number       INTEGER NOT NULL,
        comment_idx     INTEGER NOT NULL,
        author          VARCHAR,
        body            VARCHAR,
        path            VARCHAR,
        line            INTEGER,
        diff_hunk       VARCHAR,
        created_at      TIMESTAMPTZ,
        url             VARCHAR,
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, pr_number, comment_idx, refresh_id)
    )
    """,
    "CREATE INDEX github_pr_review_comment_pr ON github_pr_review_comment(project, pr_number)",
    "CREATE INDEX github_pr_review_comment_path ON github_pr_review_comment(path)",
    "CREATE INDEX github_pr_review_comment_refresh_id ON github_pr_review_comment(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # spotify_daily — daily listening aggregation from Spotify streams
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE spotify_daily (
        date               DATE NOT NULL,
        track_count        INTEGER NOT NULL DEFAULT 0,
        minutes_played     DOUBLE NOT NULL DEFAULT 0.0,
        unique_artists     INTEGER NOT NULL DEFAULT 0,
        unique_tracks      INTEGER NOT NULL DEFAULT 0,
        top_artists        VARCHAR[] NOT NULL DEFAULT [],
        top_tracks         VARCHAR[] NOT NULL DEFAULT [],
        refresh_id         VARCHAR NOT NULL,
        materialized_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (date, refresh_id)
    )
    """,
    "CREATE INDEX spotify_daily_date ON spotify_daily(date)",
    "CREATE INDEX spotify_daily_refresh_id ON spotify_daily(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # personal_daily_signal — normalized daily metrics for canonical personal products
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE personal_daily_signal (
        source             VARCHAR NOT NULL,
        date               DATE NOT NULL,
        metric             VARCHAR NOT NULL,
        value              DOUBLE NOT NULL DEFAULT 0.0,
        dimensions         JSON NOT NULL DEFAULT '{}',
        dimension_key      VARCHAR NOT NULL DEFAULT '{}',
        refresh_id         VARCHAR NOT NULL,
        materialized_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (source, date, metric, dimension_key, refresh_id)
    )
    """,
    "CREATE INDEX personal_daily_signal_source_date ON personal_daily_signal(source, date)",
    "CREATE INDEX personal_daily_signal_refresh_id ON personal_daily_signal(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # operator_day — wide cross-source daily matrix (materialized
    # operator_daily_matrix) for fast correlation queries. Nullable signal
    # columns store NULL when absent so missing stays distinct from zero.
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE operator_day (
        date                       DATE NOT NULL,
        aw_active_hours            DOUBLE,
        aw_deep_work_min           DOUBLE,
        aw_fragmentation           DOUBLE,
        git_commits                INTEGER NOT NULL DEFAULT 0,
        git_lines_added            INTEGER NOT NULL DEFAULT 0,
        git_lines_deleted          INTEGER NOT NULL DEFAULT 0,
        svn_commits                INTEGER NOT NULL DEFAULT 0,
        stress_mean                DOUBLE,
        hr_mean_bpm                DOUBLE,
        hr_resting_bpm             DOUBLE,
        hrv_sdnn                   DOUBLE,
        hrv_rmssd                  DOUBLE,
        sleep_hours                DOUBLE,
        sleep_score                DOUBLE,
        steps                      INTEGER,
        substance_doses            INTEGER NOT NULL DEFAULT 0,
        substance_mg_by_name       VARCHAR,  -- JSON object {substance: mg}; see substrate/personal.py
        wykop_comments             INTEGER NOT NULL DEFAULT 0,
        reddit_comments            INTEGER NOT NULL DEFAULT 0,
        sms_sent                   INTEGER NOT NULL DEFAULT 0,
        messenger_sent             INTEGER NOT NULL DEFAULT 0,
        outlook_inbox              INTEGER NOT NULL DEFAULT 0,
        polylogue_sessions         INTEGER NOT NULL DEFAULT 0,
        polylogue_engaged_minutes  DOUBLE NOT NULL DEFAULT 0.0,
        web_visits                 INTEGER NOT NULL DEFAULT 0,
        web_social_visits          INTEGER NOT NULL DEFAULT 0,
        shell_commands             INTEGER NOT NULL DEFAULT 0,
        spotify_hours              DOUBLE,
        keylog_keypresses          INTEGER NOT NULL DEFAULT 0,
        clipboard_entries          INTEGER NOT NULL DEFAULT 0,
        irc_lines                  INTEGER NOT NULL DEFAULT 0,
        raw_log_entries            INTEGER NOT NULL DEFAULT 0,
        substance_unique_count     INTEGER NOT NULL DEFAULT 0,
        stress_min                 DOUBLE,
        stress_max                 DOUBLE,
        web_unique_domains         INTEGER NOT NULL DEFAULT 0,
        polylogue_messages         INTEGER NOT NULL DEFAULT 0,
        weather_temp_mean          DOUBLE,
        weather_precip_mm          DOUBLE,
        weather_sunshine_hours     DOUBLE,
        weather_cloud_pct          DOUBLE,
        mood_sentiment             DOUBLE,
        mood_dominant_emotion      VARCHAR,
        mood_message_count         INTEGER NOT NULL DEFAULT 0,
        web_nsfw_share             DOUBLE,
        web_distraction_ratio      DOUBLE,
        web_top_category           VARCHAR,
        audio_energy               DOUBLE,
        audio_valence              DOUBLE,
        audio_danceability         DOUBLE,
        aw_outage_hours            DOUBLE,
        svn_files_changed          INTEGER NOT NULL DEFAULT 0,
        keylog_sessions            INTEGER NOT NULL DEFAULT 0,
        keylog_keybind_uses        INTEGER NOT NULL DEFAULT 0,
        spo2_pct                   DOUBLE,
        skin_temp_c                DOUBLE,
        sources_present            VARCHAR[] NOT NULL DEFAULT [],
        refresh_id                 VARCHAR NOT NULL,
        materialized_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (date, refresh_id)
    )
    """,
    "CREATE INDEX operator_day_date ON operator_day(date)",
    "CREATE INDEX operator_day_refresh_id ON operator_day(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # title_classification — canonical GPT/rules window-title metadata
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE title_classification (
        title_hash              VARCHAR NOT NULL,
        app                     VARCHAR NOT NULL,
        raw_title               VARCHAR,
        normalized_title        VARCHAR NOT NULL,
        activity                VARCHAR,
        subject                 VARCHAR,
        content_type            VARCHAR,
        attention_level         VARCHAR,
        topic_category          VARCHAR,
        platform                VARCHAR,
        mode                    VARCHAR,
        app_kind                VARCHAR,
        tool                    VARCHAR,
        domain                  VARCHAR,
        domain_category         VARCHAR,
        is_ai_tool              BOOLEAN,
        is_ai_active            BOOLEAN,
        productivity_score      DOUBLE,
        focus_score             DOUBLE,
        confidence              DOUBLE,
        classification_source   VARCHAR,
        model_version           VARCHAR,
        extra                   JSON NOT NULL DEFAULT '{}',
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (title_hash, refresh_id)
    )
    """,
    "CREATE INDEX title_classification_app_title ON title_classification(app, normalized_title)",
    "CREATE INDEX title_classification_activity ON title_classification(activity)",
    "CREATE INDEX title_classification_refresh_id ON title_classification(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # activity_content_day — daily ActivityWatch/title metadata coverage
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE activity_content_day (
        date                  DATE NOT NULL,
        focused_seconds       DOUBLE NOT NULL DEFAULT 0.0,
        matched_seconds       DOUBLE NOT NULL DEFAULT 0.0,
        gpt_matched_seconds   DOUBLE NOT NULL DEFAULT 0.0,
        unmatched_seconds     DOUBLE NOT NULL DEFAULT 0.0,
        matched_ratio         DOUBLE NOT NULL DEFAULT 0.0,
        gpt_matched_ratio     DOUBLE NOT NULL DEFAULT 0.0,
        source_counts         JSON NOT NULL DEFAULT '{}',
        refresh_id            VARCHAR NOT NULL,
        materialized_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (date, refresh_id)
    )
    """,
    "CREATE INDEX activity_content_day_date ON activity_content_day(date)",
    "CREATE INDEX activity_content_day_refresh_id ON activity_content_day(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # activity_content_bucket — per-day classified content buckets
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE activity_content_bucket (
        date              DATE NOT NULL,
        dimension         VARCHAR NOT NULL,
        label             VARCHAR NOT NULL,
        seconds           DOUBLE NOT NULL DEFAULT 0.0,
        refresh_id        VARCHAR NOT NULL,
        materialized_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (date, dimension, label, refresh_id)
    )
    """,
    "CREATE INDEX activity_content_bucket_dimension ON activity_content_bucket(dimension, label)",
    "CREATE INDEX activity_content_bucket_refresh_id ON activity_content_bucket(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # activity_title_usage — title-level ActivityWatch coverage and gaps
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE activity_title_usage (
        title_hash              VARCHAR NOT NULL,
        app                     VARCHAR NOT NULL,
        normalized_title        VARCHAR NOT NULL,
        example_title           VARCHAR,
        focused_seconds         DOUBLE NOT NULL DEFAULT 0.0,
        span_count              INTEGER NOT NULL DEFAULT 0,
        first_date              DATE,
        last_date               DATE,
        matched                 BOOLEAN NOT NULL DEFAULT FALSE,
        classification_source   VARCHAR,
        confidence              DOUBLE,
        activity                VARCHAR,
        content_type            VARCHAR,
        attention_level         VARCHAR,
        topic_category          VARCHAR,
        platform                VARCHAR,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (title_hash, app, refresh_id)
    )
    """,
    "CREATE INDEX activity_title_usage_matched ON activity_title_usage(matched)",
    "CREATE INDEX activity_title_usage_seconds ON activity_title_usage(focused_seconds)",
    "CREATE INDEX activity_title_usage_refresh_id ON activity_title_usage(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_metric_sample — host telemetry promoted from Sinnix capture
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_metric_sample (
        observed_at             TIMESTAMPTZ NOT NULL,
        host                    VARCHAR NOT NULL,
        boot_id                 VARCHAR,
        source                  VARCHAR NOT NULL,
        source_schema_version   INTEGER NOT NULL,
        cpu_package_w           DOUBLE,
        cpu_core_w              DOUBLE,
        cpu_pkg_c               DOUBLE,
        cpu_max_core_c          DOUBLE,
        gpu_power_w             DOUBLE,
        gpu_fan_pct             DOUBLE,
        gpu_temp_c              DOUBLE,
        gpu_util_pct            DOUBLE,
        gpu_pstate              VARCHAR,
        gpu_pcie_gen            INTEGER,
        gpu_pcie_width          INTEGER,
        load_1m                 DOUBLE,
        mem_total_mb            INTEGER,
        mem_used_mb             INTEGER,
        mem_avail_mb            INTEGER,
        mem_anon_mb             INTEGER,
        mem_file_cache_mb       INTEGER,
        mem_slab_reclaimable_mb INTEGER,
        mem_slab_unreclaimable_mb INTEGER,
        mem_dirty_mb            INTEGER,
        mem_writeback_mb        INTEGER,
        mem_shmem_mb            INTEGER,
        swap_used_mb            INTEGER,
        io_psi_some_avg10       DOUBLE,
        io_psi_some_avg60       DOUBLE,
        io_psi_some_avg300      DOUBLE,
        io_psi_some_total_us    DOUBLE,
        io_psi_full_avg10       DOUBLE,
        io_psi_full_avg60       DOUBLE,
        io_psi_full_avg300      DOUBLE,
        io_psi_full_total_us    DOUBLE,
        cpu_psi_some_avg60      DOUBLE,
        cpu_psi_some_avg300     DOUBLE,
        cpu_psi_some_total_us   DOUBLE,
        -- memory_psi_*_avg10 predate sinnix-fjq but were never promoted to
        -- the substrate (only avg60/avg300/total_us were) — filled in
        -- alongside the sinnix-kx4 vmstat/kill-event work since the
        -- pressure-incident detector needs avg10 resolution for memory
        -- (io_psi_*_avg10 was already present above).
        memory_psi_some_avg10       DOUBLE,
        memory_psi_some_avg60       DOUBLE,
        memory_psi_some_avg300      DOUBLE,
        memory_psi_some_total_us    DOUBLE,
        memory_psi_full_avg10       DOUBLE,
        memory_psi_full_avg60       DOUBLE,
        memory_psi_full_avg300      DOUBLE,
        memory_psi_full_total_us    DOUBLE,
        latency_oversleep_ms    DOUBLE,
        dstate_task_count       INTEGER,
        gap_codes               VARCHAR[] NOT NULL DEFAULT [],
        -- sinnix-fjq (schema v5): raw cumulative /proc/vmstat reclaim/OOM
        -- counters. Consumers compute deltas, same convention as the PSI
        -- *_total_us columns above.
        vmstat_workingset_refault_file    BIGINT,
        vmstat_workingset_refault_anon    BIGINT,
        vmstat_workingset_activate_file   BIGINT,
        vmstat_workingset_activate_anon   BIGINT,
        vmstat_pgscan_kswapd              BIGINT,
        vmstat_pgscan_direct              BIGINT,
        vmstat_pgsteal_kswapd             BIGINT,
        vmstat_pgsteal_direct             BIGINT,
        vmstat_pswpin                     BIGINT,
        vmstat_pswpout                    BIGINT,
        vmstat_allocstall_normal          BIGINT,
        vmstat_allocstall_movable         BIGINT,
        vmstat_oom_kill                   BIGINT,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (observed_at, host, source, refresh_id)
    )
    """,
    "CREATE INDEX machine_metric_sample_observed_at ON machine_metric_sample(observed_at)",
    "CREATE INDEX machine_metric_sample_host ON machine_metric_sample(host)",
    "CREATE INDEX machine_metric_sample_refresh_id ON machine_metric_sample(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_gpu_sample — 1 Hz GPU telemetry promoted from Sinnix capture
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_gpu_sample (
        observed_at             TIMESTAMPTZ NOT NULL,
        host                    VARCHAR NOT NULL,
        boot_id                 VARCHAR,
        source                  VARCHAR NOT NULL,
        gpu_power_w             DOUBLE,
        gpu_power_limit_w       DOUBLE,
        gpu_temp_c              DOUBLE,
        gpu_fan_pct             DOUBLE,
        gpu_util_pct            DOUBLE,
        gpu_mem_util_pct        DOUBLE,
        gpu_clock_mhz           DOUBLE,
        gpu_mem_clock_mhz       DOUBLE,
        gpu_pstate              VARCHAR,
        gpu_pcie_gen            INTEGER,
        gpu_pcie_width          INTEGER,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (observed_at, host, source, refresh_id)
    )
    """,
    "CREATE INDEX machine_gpu_sample_observed_at ON machine_gpu_sample(observed_at)",
    "CREATE INDEX machine_gpu_sample_host ON machine_gpu_sample(host)",
    "CREATE INDEX machine_gpu_sample_refresh_id ON machine_gpu_sample(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_service_state — sampled systemd/user-unit state
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_service_state (
        observed_at             TIMESTAMPTZ NOT NULL,
        host                    VARCHAR NOT NULL,
        boot_id                 VARCHAR,
        unit                    VARCHAR NOT NULL,
        scope                   VARCHAR NOT NULL,
        active_state            VARCHAR,
        sub_state               VARCHAR,
        main_pid                INTEGER,
        control_group           VARCHAR,
        memory_current_bytes    BIGINT,
        memory_anon_bytes       BIGINT,
        memory_file_bytes       BIGINT,
        memory_kernel_bytes     BIGINT,
        memory_slab_bytes       BIGINT,
        memory_sock_bytes       BIGINT,
        memory_shmem_bytes      BIGINT,
        memory_swapcached_bytes BIGINT,
        memory_zswap_bytes      BIGINT,
        memory_zswapped_bytes   BIGINT,
        cpu_usage_nsec          BIGINT,
        io_read_bytes           BIGINT,
        io_write_bytes          BIGINT,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (observed_at, host, scope, unit, refresh_id)
    )
    """,
    "CREATE INDEX machine_service_state_unit_time ON machine_service_state(unit, observed_at)",
    "CREATE INDEX machine_service_state_host_time ON machine_service_state(host, observed_at)",
    "CREATE INDEX machine_service_state_refresh_id ON machine_service_state(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_network_sample — integrated network probes in machine telemetry
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_network_sample (
        observed_at             TIMESTAMPTZ NOT NULL,
        host                    VARCHAR NOT NULL,
        boot_id                 VARCHAR,
        source_schema_version   INTEGER NOT NULL,
        interface               VARCHAR NOT NULL,
        gateway_ip              VARCHAR NOT NULL,
        ping                    JSON NOT NULL DEFAULT '{}',
        bloat                   JSON,
        iface                   JSON NOT NULL DEFAULT '{}',
        nic                     JSON NOT NULL DEFAULT '{}',
        tcp                     JSON NOT NULL DEFAULT '{}',
        dns_ms                  INTEGER,
        pmtu_1492               BOOLEAN,
        conntrack               JSON NOT NULL DEFAULT '{}',
        gap_codes               VARCHAR[] NOT NULL DEFAULT [],
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (observed_at, host, interface, refresh_id)
    )
    """,
    "CREATE INDEX machine_network_sample_observed_at ON machine_network_sample(observed_at)",
    "CREATE INDEX machine_network_sample_host_time ON machine_network_sample(host, observed_at)",
    "CREATE INDEX machine_network_sample_refresh_id ON machine_network_sample(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_process_io_delta_sample — bounded per-process I/O deltas
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_process_io_delta_sample (
        observed_at                 TIMESTAMPTZ NOT NULL,
        host                        VARCHAR NOT NULL,
        boot_id                     VARCHAR,
        source_schema_version       INTEGER NOT NULL,
        interval_s                  DOUBLE NOT NULL,
        pid                         INTEGER NOT NULL,
        process_start_time_ticks    BIGINT,
        comm                        VARCHAR,
        exe                         VARCHAR,
        cgroup                      VARCHAR,
        unit                        VARCHAR,
        scope                       VARCHAR,
        command_line                VARCHAR,
        read_bytes_delta            BIGINT NOT NULL,
        write_bytes_delta           BIGINT NOT NULL,
        cancelled_write_bytes_delta BIGINT NOT NULL,
        read_chars_delta            BIGINT NOT NULL,
        write_chars_delta           BIGINT NOT NULL,
        read_syscalls_delta         BIGINT NOT NULL,
        write_syscalls_delta        BIGINT NOT NULL,
        total_bytes_delta           BIGINT NOT NULL,
        total_syscalls_delta        BIGINT NOT NULL,
        refresh_id                  VARCHAR NOT NULL,
        materialized_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (observed_at, host, pid, process_start_time_ticks, refresh_id)
    )
    """,
    "CREATE INDEX machine_process_io_delta_sample_observed_at ON machine_process_io_delta_sample(observed_at)",
    "CREATE INDEX machine_process_io_delta_sample_unit_time ON machine_process_io_delta_sample(unit, observed_at)",
    "CREATE INDEX machine_process_io_delta_sample_comm_time ON machine_process_io_delta_sample(comm, observed_at)",
    "CREATE INDEX machine_process_io_delta_sample_refresh_id ON machine_process_io_delta_sample(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_process_memory_sample — bounded per-process PSS/private memory
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_process_memory_sample (
        observed_at                 TIMESTAMPTZ NOT NULL,
        host                        VARCHAR NOT NULL,
        boot_id                     VARCHAR,
        source_schema_version       INTEGER NOT NULL,
        pid                         INTEGER NOT NULL,
        process_start_time_ticks    BIGINT,
        comm                        VARCHAR,
        exe                         VARCHAR,
        cgroup                      VARCHAR,
        unit                        VARCHAR,
        scope                       VARCHAR,
        command_line                VARCHAR,
        rss_kb                      BIGINT NOT NULL,
        pss_kb                      BIGINT NOT NULL,
        pss_anon_kb                 BIGINT,
        pss_file_kb                 BIGINT,
        pss_shmem_kb                BIGINT,
        private_clean_kb            BIGINT NOT NULL,
        private_dirty_kb            BIGINT NOT NULL,
        shared_clean_kb             BIGINT NOT NULL,
        shared_dirty_kb             BIGINT NOT NULL,
        swap_kb                     BIGINT NOT NULL,
        refresh_id                  VARCHAR NOT NULL,
        materialized_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (observed_at, host, pid, process_start_time_ticks, refresh_id)
    )
    """,
    "CREATE INDEX machine_process_memory_sample_observed_at ON machine_process_memory_sample(observed_at)",
    "CREATE INDEX machine_process_memory_sample_unit_time ON machine_process_memory_sample(unit, observed_at)",
    "CREATE INDEX machine_process_memory_sample_comm_time ON machine_process_memory_sample(comm, observed_at)",
    "CREATE INDEX machine_process_memory_sample_refresh_id ON machine_process_memory_sample(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_cgroup_memory_sample — aggregate workload slice memory
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_cgroup_memory_sample (
        observed_at                 TIMESTAMPTZ NOT NULL,
        host                        VARCHAR NOT NULL,
        boot_id                     VARCHAR,
        source_schema_version       INTEGER NOT NULL,
        label                       VARCHAR NOT NULL,
        scope                       VARCHAR NOT NULL,
        control_group               VARCHAR NOT NULL,
        memory_current_bytes        BIGINT,
        memory_peak_bytes           BIGINT,
        memory_swap_current_bytes   BIGINT,
        memory_swap_peak_bytes      BIGINT,
        memory_high_bytes           BIGINT,
        memory_max_bytes            BIGINT,
        memory_anon_bytes           BIGINT,
        memory_file_bytes           BIGINT,
        memory_kernel_bytes         BIGINT,
        memory_slab_bytes           BIGINT,
        memory_sock_bytes           BIGINT,
        memory_shmem_bytes          BIGINT,
        memory_swapcached_bytes     BIGINT,
        memory_zswap_bytes          BIGINT,
        memory_zswapped_bytes       BIGINT,
        cgroup_populated            INTEGER,
        cgroup_frozen               INTEGER,
        cgroup_freeze               INTEGER,
        -- sinnix-fjq (schema v5): cumulative cgroup v2 memory.events *counts*
        -- (distinct from memory_high_bytes/memory_max_bytes above, which are
        -- configured byte limits, not event counts).
        memory_events_high          BIGINT,
        memory_events_max           BIGINT,
        memory_events_oom           BIGINT,
        memory_events_oom_kill      BIGINT,
        refresh_id                  VARCHAR NOT NULL,
        materialized_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (observed_at, host, label, refresh_id)
    )
    """,
    "CREATE INDEX machine_cgroup_memory_sample_label_time ON machine_cgroup_memory_sample(label, observed_at)",
    "CREATE INDEX machine_cgroup_memory_sample_scope_time ON machine_cgroup_memory_sample(scope, observed_at)",
    "CREATE INDEX machine_cgroup_memory_sample_refresh_id ON machine_cgroup_memory_sample(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_kill_event — earlyoom / kernel-oom / memcg-oom / systemd-oomd
    # kill events observed via journal (sinnix-fjq, schema v5)
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_kill_event (
        observed_at             TIMESTAMPTZ NOT NULL,
        host                    VARCHAR NOT NULL,
        boot_id                 VARCHAR,
        source_schema_version   INTEGER NOT NULL,
        killer                  VARCHAR NOT NULL,
        victim_comm             VARCHAR,
        victim_pid              INTEGER,
        victim_rss_mib          INTEGER,
        cgroup_path             VARCHAR,
        oom_score               INTEGER,
        raw_line                VARCHAR NOT NULL,
        -- earlyoom emits repeated escalating SIGTERM warnings against the
        -- same victim pid within the same observed_at second, with
        -- identical or near-identical killer/victim_pid/oom_score/raw_line
        -- as the victim's RSS shrinks between warnings — so those columns
        -- are NOT a safe dedup key. source_row_id is the live SQLite
        -- table's own autoincrement id, the only genuinely unique-per-row
        -- field, and is what the primary key anchors on.
        source_row_id           BIGINT NOT NULL,
        journal_cursor          VARCHAR,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (host, source_row_id, refresh_id)
    )
    """,
    "CREATE INDEX machine_kill_event_observed_at ON machine_kill_event(observed_at)",
    "CREATE INDEX machine_kill_event_killer_time ON machine_kill_event(killer, observed_at)",
    "CREATE INDEX machine_kill_event_refresh_id ON machine_kill_event(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # machine_experiment_run — immutable benchmark/stress-run manifests
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE machine_experiment_run (
        run_id                  VARCHAR NOT NULL,
        run_group_id            VARCHAR,
        host                    VARCHAR NOT NULL,
        workload                VARCHAR NOT NULL,
        command                 VARCHAR[] NOT NULL DEFAULT [],
        cwd                     VARCHAR,
        started_at              TIMESTAMPTZ NOT NULL,
        ended_at                TIMESTAMPTZ,
        monotonic_started_ns    BIGINT,
        monotonic_ended_ns      BIGINT,
        exit_status             INTEGER,
        execution_outcome       JSON NOT NULL DEFAULT '{}',
        service_profile         VARCHAR,
        cache_profile           VARCHAR,
        measurement_context     JSON NOT NULL DEFAULT '{}',
        planned_treatment       JSON NOT NULL DEFAULT '{}',
        nix_internal_json_path  VARCHAR,
        git_root                VARCHAR,
        git_head                VARCHAR,
        git_branch              VARCHAR,
        git_dirty               BOOLEAN,
        pre_state               JSON NOT NULL DEFAULT '{}',
        post_state              JSON NOT NULL DEFAULT '{}',
        notes                   VARCHAR[] NOT NULL DEFAULT [],
        validation_status       VARCHAR NOT NULL DEFAULT 'unknown',
        validation_issues       VARCHAR[] NOT NULL DEFAULT [],
        validation_warnings     VARCHAR[] NOT NULL DEFAULT [],
        manifest_validation     JSON NOT NULL DEFAULT '{}',
        manifest_path           VARCHAR NOT NULL,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (run_id, refresh_id)
    )
    """,
    "CREATE INDEX machine_experiment_run_started_at ON machine_experiment_run(started_at)",
    "CREATE INDEX machine_experiment_run_host_workload ON machine_experiment_run(host, workload)",
    "CREATE INDEX machine_experiment_run_refresh_id ON machine_experiment_run(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # work_observation — timed development work windows from live ledgers
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE work_observation (
        source                  VARCHAR NOT NULL,
        source_id               VARCHAR NOT NULL,
        work_kind               VARCHAR NOT NULL,
        project                 VARCHAR,
        command                 VARCHAR[] NOT NULL DEFAULT [],
        cwd                     VARCHAR,
        started_at              TIMESTAMPTZ NOT NULL,
        ended_at                TIMESTAMPTZ,
        duration_s              DOUBLE,
        status                  VARCHAR NOT NULL,
        exit_code               INTEGER,
        host                    VARCHAR NOT NULL,
        git_commit              VARCHAR,
        git_dirty               BOOLEAN NOT NULL DEFAULT FALSE,
        live_stage              VARCHAR,
        args                    JSON NOT NULL DEFAULT '[]',
        cpu_usage_avg           DOUBLE,
        memory_usage_max_mb     DOUBLE,
        process_cpu_usage_avg   DOUBLE,
        process_memory_usage_max_mb DOUBLE,
        root_process_cpu_usage_avg DOUBLE,
        root_process_memory_usage_max_mb DOUBLE,
        shared_nix_daemon_cpu_usage_avg DOUBLE,
        shared_nix_daemon_memory_usage_max_mb DOUBLE,
        shared_nix_build_slice_cpu_usage_avg DOUBLE,
        shared_nix_build_slice_memory_usage_max_mb DOUBLE,
        shared_background_slice_cpu_usage_avg DOUBLE,
        shared_background_slice_memory_usage_max_mb DOUBLE,
        host_cpu_pressure_some_avg10_max DOUBLE,
        host_io_pressure_some_avg10_max DOUBLE,
        host_io_pressure_full_avg10_max DOUBLE,
        host_memory_pressure_some_avg10_max DOUBLE,
        host_memory_pressure_full_avg10_max DOUBLE,
        host_block_read_mib_delta DOUBLE,
        host_block_write_mib_delta DOUBLE,
        host_block_read_iops_avg DOUBLE,
        host_block_write_iops_avg DOUBLE,
        host_block_busiest_device VARCHAR,
        host_block_busiest_device_total_mib_delta DOUBLE,
        host_block_busiest_device_read_iops_avg DOUBLE,
        host_block_busiest_device_write_iops_avg DOUBLE,
        host_block_busiest_device_weighted_io_ms_per_s DOUBLE,
        shm_free_min_mb         DOUBLE,
        shm_used_max_mb         DOUBLE,
        process_count_max       INTEGER,
        resource_sample_count   INTEGER,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (source, source_id, refresh_id)
    )
    """,
    "CREATE INDEX work_observation_started_at ON work_observation(started_at)",
    "CREATE INDEX work_observation_project_time ON work_observation(project, started_at)",
    "CREATE INDEX work_observation_status ON work_observation(status)",
    "CREATE INDEX work_observation_refresh_id ON work_observation(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # work_observation_stage — stage timing children from xtask ledgers
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE work_observation_stage (
        source                  VARCHAR NOT NULL,
        source_id               VARCHAR NOT NULL,
        invocation_source_id    VARCHAR NOT NULL,
        stage_name              VARCHAR NOT NULL,
        started_at              TIMESTAMPTZ NOT NULL,
        duration_s              DOUBLE,
        success                 BOOLEAN,
        io_full_avg10           DOUBLE,
        cpu_some_avg10          DOUBLE,
        memory_some_avg10       DOUBLE,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (source, source_id, refresh_id)
    )
    """,
    "CREATE INDEX work_observation_stage_invocation ON work_observation_stage(invocation_source_id)",
    "CREATE INDEX work_observation_stage_started_at ON work_observation_stage(started_at)",
    "CREATE INDEX work_observation_stage_refresh_id ON work_observation_stage(refresh_id)",
    # ────────────────────────────────────────────────────────────────────
    # work_observation_test_result — per-test rows from xtask ledgers
    # ────────────────────────────────────────────────────────────────────
    """
    CREATE TABLE work_observation_test_result (
        source                  VARCHAR NOT NULL,
        source_id               VARCHAR NOT NULL,
        invocation_source_id    VARCHAR NOT NULL,
        test_name               VARCHAR NOT NULL,
        package                 VARCHAR,
        status                  VARCHAR NOT NULL,
        duration_s              DOUBLE,
        attempt                 INTEGER,
        slot_name               VARCHAR,
        slot_wait_ms            INTEGER,
        cleanup_ms              INTEGER,
        failure_type            VARCHAR,
        test_mode               VARCHAR,
        nats_context            VARCHAR,
        refresh_id              VARCHAR NOT NULL,
        materialized_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (source, source_id, refresh_id)
    )
    """,
    "CREATE INDEX work_observation_test_invocation ON work_observation_test_result(invocation_source_id)",
    "CREATE INDEX work_observation_test_package ON work_observation_test_result(package)",
    "CREATE INDEX work_observation_test_status ON work_observation_test_result(status)",
    "CREATE INDEX work_observation_test_refresh_id ON work_observation_test_result(refresh_id)",
)


DDL_STATEMENTS = (
    *DDL_STATEMENTS,
    *(
        # ────────────────────────────────────────────────────────────────────
        # evidence_graph_build — one row per build, anchors nodes/edges
        # ────────────────────────────────────────────────────────────────────
        """
    CREATE TABLE evidence_graph_build (
        refresh_id      VARCHAR PRIMARY KEY,
        start_date      DATE NOT NULL,
        end_date        DATE NOT NULL,
        mode            VARCHAR NOT NULL,        -- materialized | network
        projects        VARCHAR[] NOT NULL DEFAULT [],   -- empty = all projects
        node_count      INTEGER NOT NULL DEFAULT 0,
        edge_count      INTEGER NOT NULL DEFAULT 0,
        caveats         JSON NOT NULL DEFAULT '[]',  -- list[{source, status, message}]
        generated_at    TIMESTAMPTZ NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
        "CREATE INDEX evidence_graph_build_window ON evidence_graph_build(start_date, end_date, mode)",
        "CREATE INDEX evidence_graph_build_refresh_id ON evidence_graph_build(refresh_id)",
        "CREATE INDEX evidence_graph_build_start_end_date ON evidence_graph_build(start_date, end_date)",
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
        # substrate_promotion_run — one row per promotion attempt
        # ────────────────────────────────────────────────────────────────────
        """
    CREATE TABLE substrate_promotion_run (
        refresh_id      VARCHAR PRIMARY KEY,
        status          VARCHAR NOT NULL,
        reason          VARCHAR,
        window_start    DATE,
        window_end      DATE,
        mode            VARCHAR,
        counts          JSON NOT NULL DEFAULT '{}',
        started_at      TIMESTAMPTZ NOT NULL,
        finished_at     TIMESTAMPTZ NOT NULL
    )
    """,
        "CREATE INDEX substrate_promotion_run_status ON substrate_promotion_run(status)",
        "CREATE INDEX substrate_promotion_run_started_at ON substrate_promotion_run(started_at)",
        "CREATE INDEX substrate_promotion_run_refresh_id ON substrate_promotion_run(refresh_id)",
        # ────────────────────────────────────────────────────────────────────
        # substrate_run_step — durable observability for long-running refreshes
        # ────────────────────────────────────────────────────────────────────
        """
    CREATE TABLE substrate_run_step (
        refresh_id      VARCHAR NOT NULL,
        step            VARCHAR NOT NULL,
        status          VARCHAR NOT NULL,
        message         VARCHAR,
        row_count       INTEGER,
        started_at      TIMESTAMPTZ,
        finished_at     TIMESTAMPTZ,
        recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (refresh_id, step, status, recorded_at)
    )
    """,
        "CREATE INDEX substrate_run_step_refresh ON substrate_run_step(refresh_id)",
        "CREATE INDEX substrate_run_step_status ON substrate_run_step(status)",
        # ────────────────────────────────────────────────────────────────────
        # analysis_claim — persisted claim audit surface
        # ────────────────────────────────────────────────────────────────────
        """
    CREATE TABLE analysis_claim (
        refresh_id          VARCHAR NOT NULL,
        claim_id            VARCHAR NOT NULL,
        claim_type          VARCHAR NOT NULL,
        project             VARCHAR,
        date                DATE,
        support_level       VARCHAR,
        confidence          DOUBLE NOT NULL DEFAULT 0.0,
        score               DOUBLE NOT NULL DEFAULT 0.0,
        summary             VARCHAR NOT NULL,
        source_ids          VARCHAR[] NOT NULL DEFAULT [],
        relation_ids        VARCHAR[] NOT NULL DEFAULT [],
        caveats             JSON NOT NULL DEFAULT '[]',
        payload             JSON NOT NULL DEFAULT '{}',
        materialized_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (refresh_id, claim_id)
    )
    """,
        "CREATE INDEX analysis_claim_project ON analysis_claim(project)",
        "CREATE INDEX analysis_claim_type ON analysis_claim(claim_type)",
        "CREATE INDEX analysis_claim_date ON analysis_claim(date)",
        # ────────────────────────────────────────────────────────────────────
        # substrate_source_status — per-source readiness for each promote run
        # ────────────────────────────────────────────────────────────────────
        # Records the outcome of every dataset contract and promoter call:
        # ok | empty | unavailable | error.
        # Distinguishes "source had nothing in window" (empty, normal) from "source
        # is broken" (unavailable / error). Lets MCP / readiness reports tell the
        # difference between an empty substrate and a silently failed one — fixes
        # the prior failure mode where polylogue-stale → ai_work_event=0 looked
        # identical to a successful promote with no events.
        """
    CREATE TABLE substrate_source_status (
        refresh_id      VARCHAR NOT NULL,
        source          VARCHAR NOT NULL,
        kind            VARCHAR NOT NULL DEFAULT 'stage',
        status          VARCHAR NOT NULL,
        reason          VARCHAR,
        row_count       INTEGER NOT NULL DEFAULT 0,
        window_start    DATE,
        window_end      DATE,
        recorded_at     TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (refresh_id, source, kind)
    )
    """,
        "CREATE INDEX substrate_source_status_source ON substrate_source_status(source)",
        "CREATE INDEX substrate_source_status_kind ON substrate_source_status(kind)",
        "CREATE INDEX substrate_source_status_status ON substrate_source_status(status)",
        # ────────────────────────────────────────────────────────────────────
        # sinnix_generation — one row per NixOS generation activation
        # ────────────────────────────────────────────────────────────────────
        # Promoted from /realm/data/captures/machine/generations.jsonl
        # (written by sinnix's lynchpinGenerationLog activation script).
        # Provides the join surface for "what changed at generation N?"
        # queries: given a machine_metric_sample.observed_at, find the
        # latest sinnix_generation.activated_at <= observed_at and read
        # sinnix_revision to anchor against sinnix git history.
        """
    CREATE TABLE sinnix_generation (
        host             VARCHAR NOT NULL,
        generation       VARCHAR NOT NULL,
        activated_at     TIMESTAMPTZ NOT NULL,
        store_path       VARCHAR NOT NULL,
        sinnix_revision  VARCHAR NOT NULL,
        nixos_label      VARCHAR NOT NULL,
        refresh_id       VARCHAR NOT NULL,
        materialized_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (host, activated_at, refresh_id)
    )
    """,
        "CREATE INDEX sinnix_generation_activated_at ON sinnix_generation(activated_at)",
        "CREATE INDEX sinnix_generation_generation ON sinnix_generation(generation)",
        "CREATE INDEX sinnix_generation_refresh_id ON sinnix_generation(refresh_id)",
        # ────────────────────────────────────────────────────────────────────
        # borg_drill_run — one row per random-archive deep-verify invocation
        # ────────────────────────────────────────────────────────────────────
        # Promoted from /realm/data/captures/machine/borg_drill.jsonl
        # written by sinnix-borg-drill (weekly oneshot). Tracks deep
        # chunk-content verification outcomes that the cheap
        # repository-only check cannot detect.
        """
    CREATE TABLE borg_drill_run (
        repo             VARCHAR NOT NULL,
        archive          VARCHAR NOT NULL,
        started_at       TIMESTAMPTZ NOT NULL,
        ended_at         TIMESTAMPTZ NOT NULL,
        duration_s       INTEGER NOT NULL DEFAULT 0,
        exit_code        INTEGER NOT NULL DEFAULT 0,
        status           VARCHAR NOT NULL,
        stderr_tail      VARCHAR,
        within_days      INTEGER NOT NULL DEFAULT 0,
        refresh_id       VARCHAR NOT NULL,
        materialized_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (repo, started_at, refresh_id)
    )
    """,
        "CREATE INDEX borg_drill_run_started_at ON borg_drill_run(started_at)",
        "CREATE INDEX borg_drill_run_status ON borg_drill_run(status)",
        "CREATE INDEX borg_drill_run_refresh_id ON borg_drill_run(refresh_id)",
        # ────────────────────────────────────────────────────────────────────
        # code_snapshot_run — one row per project per chisel materialization run
        # ────────────────────────────────────────────────────────────────────
        # Use refresh_id = 'latest' for overwrite semantics: DELETE WHERE
        # refresh_id = 'latest' then INSERT gives a clean current snapshot.
        """
    CREATE TABLE code_snapshot_run (
        project         VARCHAR NOT NULL,
        run_at          TIMESTAMPTZ NOT NULL,
        git_commit      VARCHAR NOT NULL,
        git_branch      VARCHAR NOT NULL,
        git_dirty       BOOLEAN NOT NULL DEFAULT FALSE,
        issues_open     INTEGER,
        issues_closed   INTEGER,
        gitlog_commits  INTEGER,
        xml_valid       BOOLEAN NOT NULL DEFAULT TRUE,
        elapsed_s       DOUBLE,
        status          VARCHAR NOT NULL,
        errors          VARCHAR,
        output_dir      VARCHAR NOT NULL,
        total_bytes     BIGINT NOT NULL DEFAULT 0,
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, refresh_id)
    )
    """,
        "CREATE INDEX code_snapshot_run_run_at ON code_snapshot_run(run_at)",
        "CREATE INDEX code_snapshot_run_project ON code_snapshot_run(project)",
        "CREATE INDEX code_snapshot_run_refresh_id ON code_snapshot_run(refresh_id)",
        # ────────────────────────────────────────────────────────────────────
        # code_snapshot_slice — one row per output file per project
        # ────────────────────────────────────────────────────────────────────
        """
    CREATE TABLE code_snapshot_slice (
        project         VARCHAR NOT NULL,
        filename        VARCHAR NOT NULL,
        kind            VARCHAR NOT NULL,
        size_bytes      BIGINT NOT NULL DEFAULT 0,
        path            VARCHAR NOT NULL,
        refresh_id      VARCHAR NOT NULL,
        materialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (project, filename, refresh_id)
    )
    """,
        "CREATE INDEX code_snapshot_slice_project ON code_snapshot_slice(project)",
        "CREATE INDEX code_snapshot_slice_kind ON code_snapshot_slice(kind)",
        "CREATE INDEX code_snapshot_slice_refresh_id ON code_snapshot_slice(refresh_id)",
    ),
)


# Domain table names — used by writers/readers/tests that iterate uniformly.
DOMAIN_TABLES: tuple[str, ...] = (
    "commit_fact",
    "file_change_fact",
    "ai_work_event",
    "symbol_change",
    "pr_review_row",
    "spotify_daily",
    "personal_daily_signal",
    "operator_day",
    "title_classification",
    "activity_content_day",
    "activity_content_bucket",
    "activity_title_usage",
    "sinnix_generation",
    "borg_drill_run",
    "machine_metric_sample",
    "machine_gpu_sample",
    "machine_service_state",
    "machine_network_sample",
    "machine_process_memory_sample",
    "machine_cgroup_memory_sample",
    "machine_experiment_run",
    "work_observation",
    "work_observation_stage",
    "work_observation_test_result",
    "evidence_graph_build",
    "evidence_node",
    "evidence_edge",
    "substrate_source_status",
    "substrate_run_step",
    "code_snapshot_run",
    "code_snapshot_slice",
    "github_issue",
    "github_issue_comment",
    "github_pr",
    "github_pr_comment",
    "github_pr_review",
    "github_pr_review_comment",
)
