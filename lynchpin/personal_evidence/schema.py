"""Caller-owned DuckDB schema for private Personal Evidence Substrates.

The caller opens the DuckDB path and passes its connection to ``apply_schema``.
This schema is intentionally separate from the rebuildable cross-source
substrate: it is the authority for a private evidence output, including its
provenance, bitemporal history, and incremental-run identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

SCHEMA_NAME = "personal_evidence"
SCHEMA_VERSION = 1

_RECORD_STATUS = "'draft', 'active', 'superseded', 'retracted', 'disputed', 'archived'"
_EPISTEMIC_STATUS = "'observed', 'reported', 'inferred', 'interpreted', 'disputed', 'retracted'"
_EXPORT_TIER = "'direct', 'export', 'normalized', 'derived'"
_COVERAGE_STATUS = "'covered', 'partial', 'gap', 'unknown', 'unavailable'"
_TEMPORAL_PRECISION = "'unknown', 'date', 'minute', 'second', 'instant', 'interval'"


DDL_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.schema_metadata (
        key VARCHAR PRIMARY KEY,
        value VARCHAR NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.incremental_run (
        run_id VARCHAR PRIMARY KEY,
        run_hash VARCHAR NOT NULL UNIQUE,
        input_hash VARCHAR NOT NULL,
        parent_run_id VARCHAR,
        producer VARCHAR NOT NULL,
        status VARCHAR NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'superseded')),
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        parameters JSON NOT NULL DEFAULT '{{}}',
        CHECK (finished_at IS NULL OR finished_at >= started_at)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.source_object (
        source_object_id VARCHAR PRIMARY KEY,
        source_system VARCHAR NOT NULL,
        source_key VARCHAR NOT NULL,
        source_revision_hash VARCHAR NOT NULL,
        object_kind VARCHAR NOT NULL,
        export_tier VARCHAR NOT NULL CHECK (export_tier IN ({_EXPORT_TIER})),
        source_authored_at TIMESTAMPTZ,
        source_authored_by VARCHAR,
        acquired_at TIMESTAMPTZ,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        temporal_precision VARCHAR NOT NULL DEFAULT 'unknown' CHECK (temporal_precision IN ({_TEMPORAL_PRECISION})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        CHECK (last_seen_at >= first_seen_at),
        UNIQUE (source_system, source_key, source_revision_hash)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.content_unit (
        content_unit_id VARCHAR PRIMARY KEY,
        source_object_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.source_object(source_object_id),
        unit_key VARCHAR NOT NULL,
        content_kind VARCHAR NOT NULL,
        content_hash VARCHAR NOT NULL,
        content JSON NOT NULL DEFAULT '{{}}',
        author_attribution VARCHAR,
        authored_at TIMESTAMPTZ,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        temporal_precision VARCHAR NOT NULL DEFAULT 'unknown' CHECK (temporal_precision IN ({_TEMPORAL_PRECISION})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        UNIQUE (source_object_id, unit_key, content_hash)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.entity (
        entity_id VARCHAR PRIMARY KEY,
        entity_kind VARCHAR NOT NULL,
        canonical_label VARCHAR NOT NULL,
        identity_hash VARCHAR NOT NULL UNIQUE,
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        authored_by VARCHAR,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.entity_alias (
        entity_alias_id VARCHAR PRIMARY KEY,
        entity_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.entity(entity_id),
        alias VARCHAR NOT NULL,
        normalized_alias VARCHAR NOT NULL,
        alias_kind VARCHAR NOT NULL,
        source_object_id VARCHAR REFERENCES {SCHEMA_NAME}.source_object(source_object_id),
        epistemic_status VARCHAR NOT NULL DEFAULT 'observed' CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        UNIQUE (entity_id, normalized_alias, alias_kind)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.episode (
        episode_id VARCHAR PRIMARY KEY,
        episode_kind VARCHAR NOT NULL,
        subject_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),
        source_object_id VARCHAR REFERENCES {SCHEMA_NAME}.source_object(source_object_id),
        authored_by VARCHAR,
        authored_at TIMESTAMPTZ,
        valid_from TIMESTAMPTZ NOT NULL,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        temporal_precision VARCHAR NOT NULL DEFAULT 'interval' CHECK (temporal_precision IN ({_TEMPORAL_PRECISION})),
        epistemic_status VARCHAR NOT NULL CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.observation (
        observation_id VARCHAR PRIMARY KEY,
        observation_kind VARCHAR NOT NULL,
        subject_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),
        episode_id VARCHAR REFERENCES {SCHEMA_NAME}.episode(episode_id),
        content_unit_id VARCHAR REFERENCES {SCHEMA_NAME}.content_unit(content_unit_id),
        observed_by VARCHAR,
        observed_at TIMESTAMPTZ NOT NULL,
        method VARCHAR NOT NULL,
        value JSON NOT NULL DEFAULT '{{}}',
        unit VARCHAR,
        confidence DOUBLE NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        temporal_precision VARCHAR NOT NULL DEFAULT 'instant' CHECK (temporal_precision IN ({_TEMPORAL_PRECISION})),
        epistemic_status VARCHAR NOT NULL CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        contradicts_observation_id VARCHAR REFERENCES {SCHEMA_NAME}.observation(observation_id),
        retracted_by_observation_id VARCHAR REFERENCES {SCHEMA_NAME}.observation(observation_id),
        retraction_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        CHECK (status <> 'retracted' OR retracted_by_observation_id IS NOT NULL OR retraction_rationale IS NOT NULL)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.claim (
        claim_id VARCHAR PRIMARY KEY,
        claim_key VARCHAR NOT NULL UNIQUE,
        claim_kind VARCHAR NOT NULL,
        subject_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),
        predicate VARCHAR NOT NULL,
        object_value JSON NOT NULL DEFAULT '{{}}',
        claim_hash VARCHAR NOT NULL,
        asserted_by VARCHAR,
        asserted_at TIMESTAMPTZ NOT NULL,
        method VARCHAR,
        confidence DOUBLE NOT NULL DEFAULT 0.5 CHECK (confidence >= 0.0 AND confidence <= 1.0),
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        temporal_precision VARCHAR NOT NULL DEFAULT 'unknown' CHECK (temporal_precision IN ({_TEMPORAL_PRECISION})),
        epistemic_status VARCHAR NOT NULL CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        contradicts_claim_id VARCHAR REFERENCES {SCHEMA_NAME}.claim(claim_id),
        supersedes_claim_id VARCHAR REFERENCES {SCHEMA_NAME}.claim(claim_id),
        retracted_by_claim_id VARCHAR REFERENCES {SCHEMA_NAME}.claim(claim_id),
        retracted_at TIMESTAMPTZ,
        retraction_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        CHECK (status <> 'retracted' OR retracted_by_claim_id IS NOT NULL OR retraction_rationale IS NOT NULL),
        UNIQUE (claim_hash, asserted_at)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.claim_evidence_edge (
        claim_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.claim(claim_id),
        evidence_kind VARCHAR NOT NULL,
        evidence_id VARCHAR NOT NULL,
        relation VARCHAR NOT NULL,
        evidence_hash VARCHAR NOT NULL,
        independence_group VARCHAR NOT NULL,
        rationale VARCHAR NOT NULL,
        weight DOUBLE NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0 AND weight <= 1.0),
        is_primary BOOLEAN NOT NULL DEFAULT FALSE,
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        added_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        retracted_at TIMESTAMPTZ,
        retraction_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (status <> 'retracted' OR retraction_rationale IS NOT NULL),
        PRIMARY KEY (claim_id, evidence_kind, evidence_id, relation)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.state_report (
        state_report_id VARCHAR PRIMARY KEY,
        state_key VARCHAR NOT NULL,
        subject_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),
        report_kind VARCHAR NOT NULL,
        state_value JSON NOT NULL DEFAULT '{{}}',
        reported_by VARCHAR,
        reported_at TIMESTAMPTZ NOT NULL,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        epistemic_status VARCHAR NOT NULL CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        supersedes_state_report_id VARCHAR REFERENCES {SCHEMA_NAME}.state_report(state_report_id),
        retracted_by_state_report_id VARCHAR REFERENCES {SCHEMA_NAME}.state_report(state_report_id),
        retraction_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        CHECK (status <> 'retracted' OR retracted_by_state_report_id IS NOT NULL OR retraction_rationale IS NOT NULL),
        UNIQUE (state_key, reported_at, record_hash)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.interpretation (
        interpretation_id VARCHAR PRIMARY KEY,
        interpretation_key VARCHAR NOT NULL UNIQUE,
        subject_kind VARCHAR NOT NULL,
        subject_id VARCHAR NOT NULL,
        interpretation_kind VARCHAR NOT NULL,
        conclusion JSON NOT NULL DEFAULT '{{}}',
        rationale VARCHAR NOT NULL,
        interpreted_by VARCHAR,
        interpreted_at TIMESTAMPTZ NOT NULL,
        method VARCHAR,
        confidence DOUBLE NOT NULL DEFAULT 0.5 CHECK (confidence >= 0.0 AND confidence <= 1.0),
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        epistemic_status VARCHAR NOT NULL DEFAULT 'interpreted' CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        contradicts_interpretation_id VARCHAR REFERENCES {SCHEMA_NAME}.interpretation(interpretation_id),
        retracted_by_interpretation_id VARCHAR REFERENCES {SCHEMA_NAME}.interpretation(interpretation_id),
        retraction_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        CHECK (status <> 'retracted' OR retracted_by_interpretation_id IS NOT NULL OR retraction_rationale IS NOT NULL)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.coverage_segment (
        coverage_segment_id VARCHAR PRIMARY KEY,
        source_system VARCHAR NOT NULL,
        coverage_kind VARCHAR NOT NULL,
        subject_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),
        export_tier VARCHAR NOT NULL CHECK (export_tier IN ({_EXPORT_TIER})),
        coverage_status VARCHAR NOT NULL CHECK (coverage_status IN ({_COVERAGE_STATUS})),
        valid_from TIMESTAMPTZ NOT NULL,
        valid_to TIMESTAMPTZ NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        observed_by VARCHAR,
        gap_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to >= valid_from),
        CHECK (coverage_status <> 'gap' OR gap_rationale IS NOT NULL),
        UNIQUE (source_system, coverage_kind, subject_entity_id, valid_from, valid_to, record_hash)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.answer_card (
        answer_card_id VARCHAR PRIMARY KEY,
        question_key VARCHAR NOT NULL,
        answer_kind VARCHAR NOT NULL,
        answer_value JSON NOT NULL DEFAULT '{{}}',
        authored_by VARCHAR,
        authored_at TIMESTAMPTZ NOT NULL,
        confidence DOUBLE NOT NULL DEFAULT 0.5 CHECK (confidence >= 0.0 AND confidence <= 1.0),
        epistemic_status VARCHAR NOT NULL CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'draft' CHECK (status IN ({_RECORD_STATUS})),
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        contradicts_answer_card_id VARCHAR REFERENCES {SCHEMA_NAME}.answer_card(answer_card_id),
        retracted_by_answer_card_id VARCHAR REFERENCES {SCHEMA_NAME}.answer_card(answer_card_id),
        retraction_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        CHECK (status <> 'retracted' OR retracted_by_answer_card_id IS NOT NULL OR retraction_rationale IS NOT NULL),
        UNIQUE (question_key, answer_kind, record_hash)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.daily_segment (
        daily_segment_id VARCHAR PRIMARY KEY,
        logical_date DATE NOT NULL,
        timezone VARCHAR NOT NULL,
        day_boundary_minutes INTEGER NOT NULL DEFAULT 0 CHECK (day_boundary_minutes >= 0 AND day_boundary_minutes < 1440),
        subject_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),
        valid_from TIMESTAMPTZ NOT NULL,
        valid_to TIMESTAMPTZ NOT NULL,
        coverage_status VARCHAR NOT NULL CHECK (coverage_status IN ({_COVERAGE_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        summary JSON NOT NULL DEFAULT '{{}}',
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to > valid_from),
        UNIQUE (logical_date, timezone, day_boundary_minutes, subject_entity_id, record_hash)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.graph_node (
        graph_node_id VARCHAR PRIMARY KEY,
        node_kind VARCHAR NOT NULL,
        canonical_kind VARCHAR NOT NULL,
        canonical_id VARCHAR NOT NULL,
        label VARCHAR NOT NULL,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        epistemic_status VARCHAR NOT NULL CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        UNIQUE (canonical_kind, canonical_id)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.graph_edge (
        graph_edge_id VARCHAR PRIMARY KEY,
        source_node_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.graph_node(graph_node_id),
        target_node_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.graph_node(graph_node_id),
        relation VARCHAR NOT NULL,
        claim_id VARCHAR REFERENCES {SCHEMA_NAME}.claim(claim_id),
        is_primary_evidence BOOLEAN NOT NULL DEFAULT FALSE,
        independence_group VARCHAR,
        rationale VARCHAR NOT NULL,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        transaction_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        transaction_to TIMESTAMPTZ,
        epistemic_status VARCHAR NOT NULL CHECK (epistemic_status IN ({_EPISTEMIC_STATUS})),
        status VARCHAR NOT NULL DEFAULT 'active' CHECK (status IN ({_RECORD_STATUS})),
        retracted_by_edge_id VARCHAR REFERENCES {SCHEMA_NAME}.graph_edge(graph_edge_id),
        retraction_rationale VARCHAR,
        provenance JSON NOT NULL DEFAULT '{{}}',
        record_hash VARCHAR NOT NULL,
        input_hash VARCHAR NOT NULL,
        first_seen_run_id VARCHAR NOT NULL,
        last_seen_run_id VARCHAR NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (source_node_id <> target_node_id),
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
        CHECK (transaction_to IS NULL OR transaction_to >= transaction_from),
        CHECK (status <> 'retracted' OR retracted_by_edge_id IS NOT NULL OR retraction_rationale IS NOT NULL),
        UNIQUE (source_node_id, target_node_id, relation, record_hash)
    )
    """,
    f"CREATE INDEX IF NOT EXISTS source_object_identity_idx ON {SCHEMA_NAME}.source_object(source_system, source_key)",
    f"CREATE INDEX IF NOT EXISTS content_unit_source_idx ON {SCHEMA_NAME}.content_unit(source_object_id)",
    f"CREATE INDEX IF NOT EXISTS claim_status_idx ON {SCHEMA_NAME}.claim(status, asserted_at)",
    f"CREATE INDEX IF NOT EXISTS claim_evidence_primary_idx ON {SCHEMA_NAME}.claim_evidence_edge(claim_id, is_primary, independence_group)",
    f"CREATE INDEX IF NOT EXISTS coverage_segment_window_idx ON {SCHEMA_NAME}.coverage_segment(source_system, valid_from, valid_to)",
    f"CREATE INDEX IF NOT EXISTS graph_node_canonical_idx ON {SCHEMA_NAME}.graph_node(canonical_kind, canonical_id)",
    f"CREATE INDEX IF NOT EXISTS graph_edge_primary_source_idx ON {SCHEMA_NAME}.graph_edge(source_node_id, is_primary_evidence)",
    f"CREATE INDEX IF NOT EXISTS graph_edge_primary_target_idx ON {SCHEMA_NAME}.graph_edge(target_node_id, is_primary_evidence)",
)


def apply_schema(conn: "duckdb.DuckDBPyConnection") -> None:
    """Create or update the private schema on the caller-owned connection."""

    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}")
    for statement in DDL_STATEMENTS:
        conn.execute(statement)
    conn.execute(
        f"""
        INSERT INTO {SCHEMA_NAME}.schema_metadata (key, value)
        VALUES ('schema_version', ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        [str(SCHEMA_VERSION)],
    )


__all__ = ["DDL_STATEMENTS", "SCHEMA_NAME", "SCHEMA_VERSION", "apply_schema"]
