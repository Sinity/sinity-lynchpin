"""Mission-contract tests for private personal evidence."""

from __future__ import annotations
from pathlib import Path
import duckdb
import pytest


def db(path: Path) -> duckdb.DuckDBPyConnection:
    from lynchpin.personal_evidence.schema import apply_schema

    conn = duckdb.connect(path)
    apply_schema(conn)
    conn.execute(
        """INSERT INTO personal_evidence.incremental_run(run_id,run_hash,input_hash,source_fingerprint,producer,status,started_at) VALUES ('r1','rh1','ih1','sf1','test','completed',CURRENT_TIMESTAMP),('r2','rh2','ih2','sf2','test','completed',CURRENT_TIMESTAMP)"""
    )
    return conn


def columns(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='personal_evidence' AND table_name=?",
            [table],
        ).fetchall()
    }


def test_exact_mission_column_contracts(tmp_path: Path) -> None:
    with db(tmp_path / "e.duckdb") as conn:
        required = {
            "source_object": "source_object_id owner source_kind provider canonical_ref native_locator snapshot_locator content_hash schema_version data_version captured_at coverage_start coverage_end collection_model freshness bytes privacy_class run_id".split(),
            "content_unit": "content_unit_id source_object_id native_unit_id provider_conversation_id session_id thread_id branch_id parent_unit_id role author_entity_id authorship_class created_at edited_at language content_hash quote_ancestry_hash text_locator bounded_text token_count".split(),
            "episode": "episode_id episode_kind title start end time_precision participants places projects summary status source_coverage confidence".split(),
            "observation": "observation_id subject_entity_id predicate object_entity_or_value event_start event_end authorship_class evidence_class source_unit_id method confidence scope".split(),
            "claim": "claim_id canonical_text domain scope valid_from valid_to status origin_class epistemic_class confidence confidence_reason first_asserted_at last_reviewed_at".split(),
            "claim_evidence_edge": "claim_evidence_edge_id claim_id evidence_kind evidence_id relation method source_object_id source_unit_id independence_group weight_rationale".split(),
            "state_report": "state_report_id state_kind value_or_text intensity start end time_precision reported_or_inferred source_unit_id context_episode_id".split(),
            "interpretation": "interpretation_id proposition origin created_at scope supporting_claim_ids counterevidence_claim_ids alternatives predictions disconfirmers status".split(),
            "coverage_segment": "coverage_segment_id source start end coverage_kind completeness known_gaps freshness reader snapshot_id".split(),
            "answer_card": "answer_card_id question as_of time_scope concise_answer expanded_answer examples counterexamples unknowns claim_ids source_refs confidence last_regenerated_at".split(),
            "daily_segment": "daily_segment_id logical_date segment_start segment_end activity_class agency_class project_or_context evidence_refs coverage confidence".split(),
        }
        temporal = {
            "event_start",
            "event_end",
            "event_time_precision",
            "asserted_at",
            "observed_at",
            "ingested_at",
            "valid_from",
            "valid_to",
            "superseded_at",
        }
        for table, names in required.items():
            assert set(names) <= columns(conn, table)
        for table in (
            "content_unit",
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
        ):
            assert temporal <= columns(conn, table)


def test_exact_enums_and_privacy_gate(tmp_path: Path) -> None:
    from lynchpin.personal_evidence.models import (
        AuthorshipClass,
        ClaimEvidenceRelation,
        ClaimStatus,
        EpistemicRole,
        PrivacyClass,
    )
    from lynchpin.personal_evidence.schema import upsert_source_object

    assert {x.value for x in AuthorshipClass} == {
        "operator_direct",
        "operator_quoted_or_forwarded",
        "third_party_direct",
        "machine_observation",
        "deterministic_derivation",
        "model_generated",
        "agent_generated",
        "unknown_authorship",
    }
    assert {x.value for x in EpistemicRole} == {
        "measured_fact",
        "contemporaneous_self_report",
        "retrospective_self_report",
        "third_party_report",
        "reported_event",
        "direct_communication",
        "derived_statistic",
        "association",
        "qualified_inference",
        "hypothesis",
        "narrative",
        "unknown",
    }
    assert {x.value for x in PrivacyClass} == {
        "raw_private",
        "analysis_private",
        "therapy_candidate_private",
        "operator_reviewed_export",
    }
    assert {x.value for x in ClaimStatus} == {
        "supported",
        "partially_supported",
        "contested",
        "retracted",
        "superseded",
        "model_only_unsubstantiated",
        "not_found_with_coverage",
        "unknown",
    }
    assert {x.value for x in ClaimEvidenceRelation} == {
        "supports",
        "contradicts",
        "qualifies",
        "contextualizes",
        "derived_from",
        "duplicates",
        "quotes",
        "retracts",
        "supersedes",
        "adopts_language_from",
    }
    with db(tmp_path / "e.duckdb") as conn:
        base = {
            "owner": "operator",
            "source_kind": "chat",
            "provider": "provider",
            "canonical_ref": "ref",
            "content_hash": "hash",
            "run_id": "r1",
        }
        source = upsert_source_object(conn, base)
        assert conn.execute(
            "SELECT privacy_class FROM personal_evidence.source_object WHERE source_object_id=?",
            [source],
        ).fetchone() == ("raw_private",)
        reviewed = upsert_source_object(
            conn,
            {
                **base,
                "canonical_ref": "reviewed",
                "privacy_class": "operator_reviewed_export",
            },
        )
        assert conn.execute(
            "SELECT privacy_class FROM personal_evidence.source_object WHERE source_object_id=?",
            [reviewed],
        ).fetchone() == ("operator_reviewed_export",)
        with pytest.raises(duckdb.ConstraintException):
            conn.execute(
                "UPDATE personal_evidence.source_object SET privacy_class='export' WHERE source_object_id=?",
                [source],
            )


def test_alias_quote_bitemporal_and_idempotent_incremental_rerun(
    tmp_path: Path,
) -> None:
    from lynchpin.personal_evidence.schema import upsert_source_object

    with db(tmp_path / "e.duckdb") as conn:
        values = {
            "owner": "operator",
            "source_kind": "chat",
            "provider": "provider",
            "canonical_ref": "ref",
            "content_hash": "hash",
            "run_id": "r1",
            "freshness": "old",
        }
        source = upsert_source_object(conn, values)
        assert source == upsert_source_object(
            conn, {**values, "run_id": "r2", "freshness": "new"}
        )
        assert conn.execute(
            "SELECT count(*) FROM personal_evidence.source_object"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT run_id FROM personal_evidence.source_object_seen ORDER BY run_id"
        ).fetchall() == [("r1",), ("r2",)]
        conn.execute(
            "INSERT INTO personal_evidence.entity(entity_id,entity_kind,canonical_name,record_hash,run_id,first_seen_run_id,last_seen_run_id) VALUES ('e','person','operator','eh','r1','r1','r1')"
        )
        conn.execute(
            "INSERT INTO personal_evidence.entity_alias(entity_alias_id,entity_id,alias,alias_kind,source_object_id,valid_from,valid_to,resolution_method,record_hash,run_id) VALUES ('a','e','alias','handle',?,?,?,?,?,?)",
            [source, "2026-01-01", "2026-02-01", "explicit_source", "ah", "r1"],
        )
        conn.execute(
            "INSERT INTO personal_evidence.content_unit(content_unit_id,source_object_id,role,authorship_class,content_hash,quote_ancestry_hash,record_hash,run_id,first_seen_run_id,last_seen_run_id,event_start,event_end) VALUES ('u',?,'user','operator_direct','ch','quoted','uh','r1','r1','r1','2026-01-01','2026-01-02')",
            [source],
        )
        assert (
            upsert_source_object(
                conn, {**values, "run_id": "r2", "freshness": "after-content"}
            )
            == source
        )
        assert conn.execute(
            "SELECT run_id FROM personal_evidence.source_object_seen ORDER BY run_id"
        ).fetchall() == [("r1",), ("r2",)]
        alias, quote_hash, event_start, event_end = conn.execute(
            "SELECT alias,quote_ancestry_hash,event_start,event_end FROM personal_evidence.entity_alias a JOIN personal_evidence.content_unit u ON TRUE"
        ).fetchone()
        assert (alias, quote_hash, event_start.date(), event_end.date()) == (
            "alias",
            "quoted",
            __import__("datetime").date(2026, 1, 1),
            __import__("datetime").date(2026, 1, 2),
        )


def test_graph_relations_cover_primary_contradiction_copy_dependency_and_gap(
    tmp_path: Path,
) -> None:
    from lynchpin.personal_evidence import schema

    assert set(schema.N.replace("'", "").split(",")) == {
        "source_object",
        "content_unit",
        "entity",
        "episode",
        "observation",
        "claim",
        "interpretation",
        "answer_card",
        "logical_day",
        "life_phase",
    }
    assert set(schema.G.replace("'", "").split(",")) == {
        "provenance",
        "temporal_containment",
        "participation",
        "support",
        "contradiction",
        "derivation",
        "succession",
        "relationship",
        "model_copy_tracing",
        "answer_card_dependency",
        "source_gap_linkage",
    }
    assert set(schema.K.replace("'", "").split(",")) == {
        "person",
        "project",
        "institution",
        "place",
        "medication",
        "substance",
        "diagnosis_or_label",
        "symptom_or_experience",
        "value",
        "goal",
        "activity",
        "device",
        "source",
        "concept",
    }
    with db(tmp_path / "e.duckdb") as conn:
        conn.execute(
            "INSERT INTO personal_evidence.claim(claim_id,canonical_text,domain,scope,status,origin_class,epistemic_class,confidence_reason,record_hash,run_id,first_seen_run_id,last_seen_run_id) VALUES ('c','text','d','s','supported','operator_direct','contemporaneous_self_report','why','ch','r1','r1','r1')"
        )
        kinds = ["source_object", "content_unit", "claim", "answer_card", "logical_day"]
        conn.executemany(
            "INSERT INTO personal_evidence.graph_node(graph_node_id,node_kind,canonical_id,label,record_hash,run_id,first_seen_run_id,last_seen_run_id) VALUES (?,?,?,?,?,?,?,?)",
            [
                (f"n{i}", k, k, k, f"h{i}", "r1", "r1", "r1")
                for i, k in enumerate(kinds)
            ],
        )
        edges = [
            ("e1", "n2", "n1", "provenance", True),
            ("e2", "n2", "n3", "contradiction", False),
            ("e3", "n2", "n0", "model_copy_tracing", False),
            ("e4", "n3", "n2", "answer_card_dependency", False),
            ("e5", "n4", "n0", "source_gap_linkage", False),
        ]
        conn.executemany(
            "INSERT INTO personal_evidence.graph_edge(graph_edge_id,source_node_id,target_node_id,relation,is_primary_evidence,rationale,record_hash,run_id,first_seen_run_id,last_seen_run_id) VALUES (?,?,?,?,?,'why',?,?,?,?)",
            [(a, b, c, d, e, a + "h", "r1", "r1", "r1") for a, b, c, d, e in edges],
        )
        assert {
            r[0]
            for r in conn.execute(
                "SELECT relation FROM personal_evidence.graph_edge"
            ).fetchall()
        } == {x[3] for x in edges}
        assert conn.execute(
            "WITH RECURSIVE p(id) AS (SELECT 'n2' UNION ALL SELECT target_node_id FROM personal_evidence.graph_edge JOIN p ON source_node_id=id WHERE is_primary_evidence) SELECT id FROM p ORDER BY id"
        ).fetchall() == [("n1",), ("n2",)]
