"""Contract tests for the private Personal Evidence Substrate schema."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

UTC = timezone.utc


def _connection(path: Path) -> duckdb.DuckDBPyConnection:
    from lynchpin.personal_evidence.schema import apply_schema

    conn = duckdb.connect(path)
    apply_schema(conn)
    return conn


def _source_object_values(*, record_hash: str = "record-a", last_seen_run_id: str = "run-a") -> tuple[str, ...]:
    return (
        "source-1",
        "neutral-source",
        "source-key-1",
        "revision-a",
        "record",
        "export",
        record_hash,
        "input-a",
        "run-a",
        last_seen_run_id,
    )


def test_apply_schema_creates_all_core_entities(tmp_path: Path) -> None:
    from lynchpin.personal_evidence.schema import SCHEMA_NAME, SCHEMA_VERSION

    with _connection(tmp_path / "private.duckdb") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                [SCHEMA_NAME],
            ).fetchall()
        }
        version = conn.execute(
            f"SELECT value FROM {SCHEMA_NAME}.schema_metadata WHERE key = 'schema_version'"
        ).fetchone()

    assert {
        "source_object",
        "content_unit",
        "entity",
        "entity_alias",
        "episode",
        "observation",
        "claim",
        "claim_evidence_edge",
        "state_report",
        "interpretation",
        "coverage_segment",
        "answer_card",
        "daily_segment",
        "graph_node",
        "graph_edge",
        "incremental_run",
    } <= tables
    assert version == (str(SCHEMA_VERSION),)


def test_constraints_reject_invalid_temporal_range_and_export_tier(tmp_path: Path) -> None:
    from lynchpin.personal_evidence.schema import SCHEMA_NAME

    with _connection(tmp_path / "private.duckdb") as conn:
        with pytest.raises(duckdb.ConstraintException):
            conn.execute(
                f"""
                INSERT INTO {SCHEMA_NAME}.source_object (
                    source_object_id, source_system, source_key, source_revision_hash,
                    object_kind, export_tier, valid_from, valid_to, record_hash,
                    input_hash, first_seen_run_id, last_seen_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *_source_object_values()[:6],
                    "2026-01-02T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                    *_source_object_values()[6:],
                ),
            )
        with pytest.raises(duckdb.ConstraintException):
            conn.execute(
                f"""
                INSERT INTO {SCHEMA_NAME}.source_object (
                    source_object_id, source_system, source_key, source_revision_hash,
                    object_kind, export_tier, record_hash, input_hash,
                    first_seen_run_id, last_seen_run_id
                ) VALUES (?, ?, ?, ?, ?, 'unsupported', ?, ?, ?, ?)
                """,
                (*_source_object_values()[:5], *_source_object_values()[6:]),
            )


def test_claim_preserves_distinct_valid_and_transaction_time(tmp_path: Path) -> None:
    from lynchpin.personal_evidence.schema import SCHEMA_NAME

    valid_from = datetime(2026, 1, 3, 9, tzinfo=UTC)
    transaction_from = datetime(2026, 1, 4, 10, tzinfo=UTC)
    with _connection(tmp_path / "private.duckdb") as conn:
        conn.execute(
            f"""
            INSERT INTO {SCHEMA_NAME}.claim (
                claim_id, claim_key, claim_kind, predicate, claim_hash, asserted_at,
                valid_from, transaction_from, epistemic_status, record_hash, input_hash,
                first_seen_run_id, last_seen_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "claim-1",
                "claim-key-1",
                "statement",
                "relates_to",
                "claim-hash-a",
                transaction_from,
                valid_from,
                transaction_from,
                "reported",
                "record-a",
                "input-a",
                "run-a",
                "run-a",
            ],
        )
        row = conn.execute(
            f"SELECT valid_from, transaction_from FROM {SCHEMA_NAME}.claim WHERE claim_id = 'claim-1'"
        ).fetchone()

    assert row is not None
    assert row[0] != row[1]
    assert row[0].astimezone(UTC) == valid_from
    assert row[1].astimezone(UTC) == transaction_from


def test_source_object_supports_incremental_upsert_identity(tmp_path: Path) -> None:
    from lynchpin.personal_evidence.schema import SCHEMA_NAME

    with _connection(tmp_path / "private.duckdb") as conn:
        conn.execute(
            f"""
            INSERT INTO {SCHEMA_NAME}.source_object (
                source_object_id, source_system, source_key, source_revision_hash,
                object_kind, export_tier, record_hash, input_hash,
                first_seen_run_id, last_seen_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _source_object_values(),
        )
        conn.execute(
            f"""
            INSERT INTO {SCHEMA_NAME}.source_object (
                source_object_id, source_system, source_key, source_revision_hash,
                object_kind, export_tier, record_hash, input_hash,
                first_seen_run_id, last_seen_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_object_id) DO NOTHING
            """,
            _source_object_values(record_hash="record-b", last_seen_run_id="run-b"),
        )
        conn.execute(
            f"""
            UPDATE {SCHEMA_NAME}.source_object
            SET record_hash = ?, last_seen_run_id = ?, last_seen_at = CURRENT_TIMESTAMP
            WHERE source_object_id = ?
            """,
            ["record-b", "run-b", "source-1"],
        )
        rows = conn.execute(
            f"SELECT record_hash, first_seen_run_id, last_seen_run_id FROM {SCHEMA_NAME}.source_object"
        ).fetchall()

    assert rows == [("record-b", "run-a", "run-b")]


def test_primary_evidence_edges_support_recursive_graph_traversal(tmp_path: Path) -> None:
    from lynchpin.personal_evidence.schema import SCHEMA_NAME

    with _connection(tmp_path / "private.duckdb") as conn:
        conn.executemany(
            f"""
            INSERT INTO {SCHEMA_NAME}.graph_node (
                graph_node_id, node_kind, canonical_kind, canonical_id, label,
                epistemic_status, record_hash, input_hash, first_seen_run_id, last_seen_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("claim-node", "claim", "claim", "claim-1", "claim", "reported", "node-a", "input-a", "run-a", "run-a"),
                ("observation-node", "observation", "observation", "observation-1", "observation", "observed", "node-b", "input-a", "run-a", "run-a"),
                ("content-node", "content", "content_unit", "content-1", "content", "observed", "node-c", "input-a", "run-a", "run-a"),
            ],
        )
        conn.executemany(
            f"""
            INSERT INTO {SCHEMA_NAME}.graph_edge (
                graph_edge_id, source_node_id, target_node_id, relation,
                is_primary_evidence, rationale, epistemic_status, record_hash,
                input_hash, first_seen_run_id, last_seen_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("edge-a", "claim-node", "observation-node", "supported_by", True, "direct support", "observed", "edge-a", "input-a", "run-a", "run-a"),
                ("edge-b", "observation-node", "content-node", "derived_from", True, "source content", "observed", "edge-b", "input-a", "run-a", "run-a"),
            ],
        )
        traversed = conn.execute(
            f"""
            WITH RECURSIVE primary_path(node_id, depth) AS (
                SELECT 'claim-node', 0
                UNION ALL
                SELECT edge.target_node_id, primary_path.depth + 1
                FROM {SCHEMA_NAME}.graph_edge AS edge
                JOIN primary_path ON edge.source_node_id = primary_path.node_id
                WHERE edge.is_primary_evidence AND edge.status = 'active'
            )
            SELECT node_id FROM primary_path ORDER BY depth
            """
        ).fetchall()

    assert traversed == [("claim-node",), ("observation-node",), ("content-node",)]
