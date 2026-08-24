"""Mission-exact DuckDB contract for private personal evidence."""

from __future__ import annotations
from hashlib import sha256
from json import dumps
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    import duckdb

SCHEMA_NAME = "personal_evidence"
SCHEMA_VERSION = 2
A = "'operator_direct','operator_quoted_or_forwarded','third_party_direct','machine_observation','deterministic_derivation','model_generated','agent_generated','unknown_authorship'"
E = "'measured_fact','contemporaneous_self_report','retrospective_self_report','third_party_report','reported_event','direct_communication','derived_statistic','association','qualified_inference','hypothesis','narrative','unknown'"
P = "'raw_private','analysis_private','therapy_candidate_private','operator_reviewed_export'"
C = "'supported','partially_supported','contested','retracted','superseded','model_only_unsubstantiated','not_found_with_coverage','unknown'"
CE = "'supports','contradicts','qualifies','contextualizes','derived_from','duplicates','quotes','retracts','supersedes','adopts_language_from'"
N = "'source_object','content_unit','entity','episode','observation','claim','interpretation','answer_card','logical_day','life_phase'"
G = "'provenance','temporal_containment','participation','support','contradiction','derivation','succession','relationship','model_copy_tracing','answer_card_dependency','source_gap_linkage'"
K = "'person','project','institution','place','medication','substance','diagnosis_or_label','symptom_or_experience','value','goal','activity','device','source','concept'"
T = "'unknown','date','minute','second','instant','interval'"
R = "'active','superseded','retracted','archived','unknown'"
V = "'covered','partial','gap','unknown','unavailable'"
B = """event_start TIMESTAMPTZ,event_end TIMESTAMPTZ,event_time_precision VARCHAR NOT NULL DEFAULT 'unknown' CHECK(event_time_precision IN ({T})),asserted_at TIMESTAMPTZ,observed_at TIMESTAMPTZ,ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,valid_from TIMESTAMPTZ,valid_to TIMESTAMPTZ,superseded_at TIMESTAMPTZ,CHECK(event_end IS NULL OR event_start IS NULL OR event_end>=event_start),CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to>=valid_from)"""
INCREMENTAL = """record_hash VARCHAR NOT NULL UNIQUE,run_id VARCHAR NOT NULL REFERENCES {s}.incremental_run(run_id),first_seen_run_id VARCHAR NOT NULL REFERENCES {s}.incremental_run(run_id),last_seen_run_id VARCHAR NOT NULL REFERENCES {s}.incremental_run(run_id),first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"""
I = INCREMENTAL  # noqa: E741


def table(name: str, columns: str) -> str:
    return f"CREATE TABLE IF NOT EXISTS {SCHEMA_NAME}.{name} ({columns})"


DDL_STATEMENTS = (
    table(
        "schema_metadata",
        "key VARCHAR PRIMARY KEY,value VARCHAR NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
    ),
    table(
        "incremental_run",
        "run_id VARCHAR PRIMARY KEY,run_hash VARCHAR NOT NULL UNIQUE,input_hash VARCHAR NOT NULL,parent_run_id VARCHAR,source_fingerprint VARCHAR NOT NULL,snapshot_id VARCHAR,high_water_mark VARCHAR,extraction_version VARCHAR,model_version VARCHAR,prompt_version VARCHAR,producer VARCHAR NOT NULL,status VARCHAR NOT NULL CHECK(status IN ('running','completed','failed','superseded')),started_at TIMESTAMPTZ NOT NULL,finished_at TIMESTAMPTZ,manifest JSON NOT NULL DEFAULT '{}',CHECK(finished_at IS NULL OR finished_at>=started_at)",
    ),
    table(
        "source_object",
        f"""source_object_id VARCHAR PRIMARY KEY,owner VARCHAR NOT NULL,source_kind VARCHAR NOT NULL,provider VARCHAR NOT NULL,canonical_ref VARCHAR NOT NULL,native_locator VARCHAR,snapshot_locator VARCHAR,content_hash VARCHAR NOT NULL,schema_version VARCHAR,data_version VARCHAR,captured_at TIMESTAMPTZ,coverage_start TIMESTAMPTZ,coverage_end TIMESTAMPTZ,collection_model VARCHAR,freshness VARCHAR,bytes BIGINT CHECK(bytes IS NULL OR bytes>=0),privacy_class VARCHAR NOT NULL DEFAULT 'raw_private' CHECK(privacy_class IN ({P})),run_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.incremental_run(run_id),ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,CHECK(coverage_end IS NULL OR coverage_start IS NULL OR coverage_end>=coverage_start)""",
    ),
    table(
        "source_object_seen",
        f"source_object_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.source_object(source_object_id),run_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.incremental_run(run_id),last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(source_object_id,run_id)",
    ),
    table(
        "entity",
        f"entity_id VARCHAR PRIMARY KEY,entity_kind VARCHAR NOT NULL CHECK(entity_kind IN ({K})),canonical_name VARCHAR NOT NULL,description VARCHAR,source_object_id VARCHAR REFERENCES {SCHEMA_NAME}.source_object(source_object_id),{B.format(T=T)},{INCREMENTAL.format(s=SCHEMA_NAME)}",
    ),
    table(
        "entity_alias",
        f"entity_alias_id VARCHAR PRIMARY KEY,entity_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.entity(entity_id),alias VARCHAR NOT NULL,alias_kind VARCHAR NOT NULL,source_object_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.source_object(source_object_id),source_unit_id VARCHAR,valid_from TIMESTAMPTZ,valid_to TIMESTAMPTZ,asserted_at TIMESTAMPTZ,ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,resolution_method VARCHAR NOT NULL,confidence DOUBLE NOT NULL DEFAULT 1 CHECK(confidence BETWEEN 0 AND 1),record_hash VARCHAR NOT NULL UNIQUE,run_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.incremental_run(run_id),CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to>=valid_from)",
    ),
    table(
        "content_unit",
        f"content_unit_id VARCHAR PRIMARY KEY,source_object_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.source_object(source_object_id),native_unit_id VARCHAR,provider_conversation_id VARCHAR,session_id VARCHAR,thread_id VARCHAR,branch_id VARCHAR,parent_unit_id VARCHAR,role VARCHAR NOT NULL,author_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),authorship_class VARCHAR NOT NULL CHECK(authorship_class IN ({A})),created_at TIMESTAMPTZ,edited_at TIMESTAMPTZ,language VARCHAR,content_hash VARCHAR NOT NULL,quote_ancestry_hash VARCHAR,text_locator VARCHAR,bounded_text VARCHAR,token_count BIGINT CHECK(token_count IS NULL OR token_count>=0),{B.format(T=T)},{INCREMENTAL.format(s=SCHEMA_NAME)},UNIQUE(source_object_id,native_unit_id,content_hash)",
    ),
    table(
        "episode",
        f"episode_id VARCHAR PRIMARY KEY,episode_kind VARCHAR NOT NULL,title VARCHAR NOT NULL,start TIMESTAMPTZ,\"end\" TIMESTAMPTZ,time_precision VARCHAR NOT NULL CHECK(time_precision IN ({T})),participants JSON NOT NULL DEFAULT '[]',places JSON NOT NULL DEFAULT '[]',projects JSON NOT NULL DEFAULT '[]',summary VARCHAR,status VARCHAR NOT NULL DEFAULT 'active' CHECK(status IN ({R})),source_coverage JSON NOT NULL DEFAULT '{{}}',confidence DOUBLE NOT NULL DEFAULT .5 CHECK(confidence BETWEEN 0 AND 1),{B.format(T=T)},{INCREMENTAL.format(s=SCHEMA_NAME)},CHECK(\"end\" IS NULL OR start IS NULL OR \"end\">=start)",
    ),
    table(
        "observation",
        f"observation_id VARCHAR PRIMARY KEY,subject_entity_id VARCHAR REFERENCES {SCHEMA_NAME}.entity(entity_id),predicate VARCHAR NOT NULL,object_entity_or_value JSON NOT NULL,event_start TIMESTAMPTZ,event_end TIMESTAMPTZ,authorship_class VARCHAR NOT NULL CHECK(authorship_class IN ({A})),evidence_class VARCHAR NOT NULL CHECK(evidence_class IN ({E})),source_unit_id VARCHAR REFERENCES {SCHEMA_NAME}.content_unit(content_unit_id),method VARCHAR NOT NULL,confidence DOUBLE NOT NULL DEFAULT .5 CHECK(confidence BETWEEN 0 AND 1),scope VARCHAR NOT NULL,event_time_precision VARCHAR NOT NULL DEFAULT 'unknown' CHECK(event_time_precision IN ({T})),asserted_at TIMESTAMPTZ,observed_at TIMESTAMPTZ,ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,valid_from TIMESTAMPTZ,valid_to TIMESTAMPTZ,superseded_at TIMESTAMPTZ,status VARCHAR NOT NULL DEFAULT 'active' CHECK(status IN ({R})),{INCREMENTAL.format(s=SCHEMA_NAME)},CHECK(event_end IS NULL OR event_start IS NULL OR event_end>=event_start),CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to>=valid_from)",
    ),
    table(
        "claim",
        f"claim_id VARCHAR PRIMARY KEY,canonical_text VARCHAR NOT NULL,domain VARCHAR NOT NULL,scope VARCHAR NOT NULL,valid_from TIMESTAMPTZ,valid_to TIMESTAMPTZ,status VARCHAR NOT NULL CHECK(status IN ({C})),origin_class VARCHAR NOT NULL CHECK(origin_class IN ({A})),epistemic_class VARCHAR NOT NULL CHECK(epistemic_class IN ({E})),confidence DOUBLE NOT NULL DEFAULT .5 CHECK(confidence BETWEEN 0 AND 1),confidence_reason VARCHAR NOT NULL,first_asserted_at TIMESTAMPTZ,last_reviewed_at TIMESTAMPTZ,event_start TIMESTAMPTZ,event_end TIMESTAMPTZ,event_time_precision VARCHAR NOT NULL DEFAULT 'unknown' CHECK(event_time_precision IN ({T})),asserted_at TIMESTAMPTZ,observed_at TIMESTAMPTZ,ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,superseded_at TIMESTAMPTZ,{INCREMENTAL.format(s=SCHEMA_NAME)},CHECK(event_end IS NULL OR event_start IS NULL OR event_end>=event_start),CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to>=valid_from)",
    ),
    table(
        "claim_evidence_edge",
        f"claim_evidence_edge_id VARCHAR PRIMARY KEY,claim_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.claim(claim_id),evidence_kind VARCHAR NOT NULL,evidence_id VARCHAR NOT NULL,relation VARCHAR NOT NULL CHECK(relation IN ({CE})),method VARCHAR NOT NULL,source_object_id VARCHAR REFERENCES {SCHEMA_NAME}.source_object(source_object_id),source_unit_id VARCHAR REFERENCES {SCHEMA_NAME}.content_unit(content_unit_id),independence_group VARCHAR NOT NULL,weight_rationale VARCHAR NOT NULL,weight DOUBLE CHECK(weight IS NULL OR weight BETWEEN 0 AND 1),{B.format(T=T)},{I.format(s=SCHEMA_NAME)},UNIQUE(claim_id,evidence_kind,evidence_id,relation)",
    ),
    table(
        "state_report",
        f"state_report_id VARCHAR PRIMARY KEY,state_kind VARCHAR NOT NULL,value_or_text VARCHAR NOT NULL,intensity DOUBLE CHECK(intensity IS NULL OR intensity BETWEEN 0 AND 1),start TIMESTAMPTZ,\"end\" TIMESTAMPTZ,time_precision VARCHAR NOT NULL CHECK(time_precision IN ({T})),reported_or_inferred VARCHAR NOT NULL CHECK(reported_or_inferred IN ('reported','inferred')),source_unit_id VARCHAR REFERENCES {SCHEMA_NAME}.content_unit(content_unit_id),context_episode_id VARCHAR REFERENCES {SCHEMA_NAME}.episode(episode_id),{B.format(T=T)},{I.format(s=SCHEMA_NAME)},status VARCHAR NOT NULL DEFAULT 'active' CHECK(status IN ({R})),CHECK(\"end\" IS NULL OR start IS NULL OR \"end\">=start)",
    ),
    table(
        "interpretation",
        f"interpretation_id VARCHAR PRIMARY KEY,proposition VARCHAR NOT NULL,origin VARCHAR NOT NULL,created_at TIMESTAMPTZ NOT NULL,scope VARCHAR NOT NULL,supporting_claim_ids JSON NOT NULL DEFAULT '[]',counterevidence_claim_ids JSON NOT NULL DEFAULT '[]',alternatives JSON NOT NULL DEFAULT '[]',predictions JSON NOT NULL DEFAULT '[]',disconfirmers JSON NOT NULL DEFAULT '[]',status VARCHAR NOT NULL DEFAULT 'active' CHECK(status IN ({R})),authorship_class VARCHAR NOT NULL CHECK(authorship_class IN ({A})),epistemic_class VARCHAR NOT NULL CHECK(epistemic_class IN ({E})),{B.format(T=T)},{I.format(s=SCHEMA_NAME)}",
    ),
    table(
        "coverage_segment",
        f'coverage_segment_id VARCHAR PRIMARY KEY,source VARCHAR NOT NULL,start TIMESTAMPTZ,"end" TIMESTAMPTZ,coverage_kind VARCHAR NOT NULL,completeness VARCHAR NOT NULL CHECK(completeness IN ({V})),known_gaps JSON NOT NULL DEFAULT \'[]\',freshness VARCHAR,reader VARCHAR NOT NULL,snapshot_id VARCHAR,source_object_id VARCHAR REFERENCES {SCHEMA_NAME}.source_object(source_object_id),{B.format(T=T)},{I.format(s=SCHEMA_NAME)},CHECK("end" IS NULL OR start IS NULL OR "end">=start)',
    ),
    table(
        "answer_card",
        f"answer_card_id VARCHAR PRIMARY KEY,question VARCHAR NOT NULL,as_of TIMESTAMPTZ NOT NULL,time_scope VARCHAR NOT NULL,concise_answer VARCHAR,expanded_answer VARCHAR,examples JSON NOT NULL DEFAULT '[]',counterexamples JSON NOT NULL DEFAULT '[]',unknowns JSON NOT NULL DEFAULT '[]',claim_ids JSON NOT NULL DEFAULT '[]',source_refs JSON NOT NULL DEFAULT '[]',confidence DOUBLE NOT NULL DEFAULT .5 CHECK(confidence BETWEEN 0 AND 1),last_regenerated_at TIMESTAMPTZ,privacy_class VARCHAR NOT NULL DEFAULT 'analysis_private' CHECK(privacy_class IN ({P})),status VARCHAR NOT NULL DEFAULT 'active' CHECK(status IN ({R})),{B.format(T=T)},{I.format(s=SCHEMA_NAME)}",
    ),
    table(
        "daily_segment",
        f"daily_segment_id VARCHAR PRIMARY KEY,logical_date DATE NOT NULL,segment_start TIMESTAMPTZ NOT NULL,segment_end TIMESTAMPTZ NOT NULL,activity_class VARCHAR NOT NULL,agency_class VARCHAR NOT NULL CHECK(agency_class IN ('direct_operator','active_supervision_or_review','delegated_agent','automated_system','passive_or_consumptive','offline_observed','mixed','unknown')),project_or_context VARCHAR,evidence_refs JSON NOT NULL DEFAULT '[]',coverage JSON NOT NULL DEFAULT '{{}}',confidence DOUBLE NOT NULL DEFAULT .5 CHECK(confidence BETWEEN 0 AND 1),{B.format(T=T)},{I.format(s=SCHEMA_NAME)},CHECK(segment_end>segment_start)",
    ),
    table(
        "graph_node",
        f"graph_node_id VARCHAR PRIMARY KEY,node_kind VARCHAR NOT NULL CHECK(node_kind IN ({N})),canonical_id VARCHAR NOT NULL,label VARCHAR NOT NULL,{B.format(T=T)},{I.format(s=SCHEMA_NAME)},UNIQUE(node_kind,canonical_id)",
    ),
    table(
        "graph_edge",
        f"graph_edge_id VARCHAR PRIMARY KEY,source_node_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.graph_node(graph_node_id),target_node_id VARCHAR NOT NULL REFERENCES {SCHEMA_NAME}.graph_node(graph_node_id),relation VARCHAR NOT NULL CHECK(relation IN ({G})),claim_id VARCHAR REFERENCES {SCHEMA_NAME}.claim(claim_id),is_primary_evidence BOOLEAN NOT NULL DEFAULT FALSE,independence_group VARCHAR,rationale VARCHAR NOT NULL,{B.format(T=T)},status VARCHAR NOT NULL DEFAULT 'active' CHECK(status IN ({R})),{I.format(s=SCHEMA_NAME)},CHECK(source_node_id<>target_node_id),UNIQUE(source_node_id,target_node_id,relation,record_hash)",
    ),
    f"CREATE INDEX IF NOT EXISTS source_object_identity_idx ON {SCHEMA_NAME}.source_object(owner,source_kind,provider,canonical_ref,content_hash)",
    f"CREATE INDEX IF NOT EXISTS content_unit_quote_idx ON {SCHEMA_NAME}.content_unit(source_object_id,quote_ancestry_hash)",
    f"CREATE INDEX IF NOT EXISTS entity_alias_validity_idx ON {SCHEMA_NAME}.entity_alias(entity_id,valid_from,valid_to)",
    f"CREATE INDEX IF NOT EXISTS claim_evidence_idx ON {SCHEMA_NAME}.claim_evidence_edge(claim_id,relation,independence_group)",
    f"CREATE INDEX IF NOT EXISTS graph_source_idx ON {SCHEMA_NAME}.graph_edge(source_node_id,relation,is_primary_evidence)",
    f"CREATE INDEX IF NOT EXISTS graph_target_idx ON {SCHEMA_NAME}.graph_edge(target_node_id,relation)",
)


def source_object_identity(
    *,
    owner: str,
    source_kind: str,
    provider: str,
    canonical_ref: str,
    content_hash: str,
) -> str:
    return sha256(
        dumps(
            [owner, source_kind, provider, canonical_ref, content_hash],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def upsert_source_object(
    conn: "duckdb.DuckDBPyConnection", values: Mapping[str, Any]
) -> str:
    required = {
        "owner",
        "source_kind",
        "provider",
        "canonical_ref",
        "content_hash",
        "run_id",
    }
    missing = required - values.keys()
    if missing:
        raise ValueError(
            f"source object missing required fields: {', '.join(sorted(missing))}"
        )
    key = [
        values[x]
        for x in ("owner", "source_kind", "provider", "canonical_ref", "content_hash")
    ]
    row = conn.execute(
        f"SELECT source_object_id FROM {SCHEMA_NAME}.source_object WHERE owner=? AND source_kind=? AND provider=? AND canonical_ref=? AND content_hash=?",
        key,
    ).fetchone()
    if row:
        conn.execute(
            f"INSERT INTO {SCHEMA_NAME}.source_object_seen(source_object_id,run_id) VALUES (?,?) ON CONFLICT(source_object_id,run_id) DO NOTHING",
            [row[0], values["run_id"]],
        )
        return str(row[0])
    source_object_id = str(
        values.get("source_object_id")
        or source_object_identity(
            owner=str(key[0]),
            source_kind=str(key[1]),
            provider=str(key[2]),
            canonical_ref=str(key[3]),
            content_hash=str(key[4]),
        )
    )
    cols = "source_object_id,owner,source_kind,provider,canonical_ref,native_locator,snapshot_locator,content_hash,schema_version,data_version,captured_at,coverage_start,coverage_end,collection_model,freshness,bytes,privacy_class,run_id"
    data = (
        [source_object_id]
        + key[:4]
        + [values.get(x) for x in ("native_locator", "snapshot_locator")]
        + [key[4]]
        + [
            values.get(x)
            for x in (
                "schema_version",
                "data_version",
                "captured_at",
                "coverage_start",
                "coverage_end",
                "collection_model",
                "freshness",
                "bytes",
            )
        ]
        + [values.get("privacy_class", "raw_private")]
        + [values["run_id"]]
    )
    conn.execute(
        f"INSERT INTO {SCHEMA_NAME}.source_object ({cols}) VALUES ({','.join('?' for _ in data)})",
        data,
    )
    conn.execute(
        f"INSERT INTO {SCHEMA_NAME}.source_object_seen(source_object_id,run_id) VALUES (?,?)",
        [source_object_id, values["run_id"]],
    )
    return source_object_id


def apply_schema(conn: "duckdb.DuckDBPyConnection") -> None:
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME}")
    found = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=? AND table_name='schema_metadata'",
        [SCHEMA_NAME],
    ).fetchone()
    if found:
        version = conn.execute(
            f"SELECT value FROM {SCHEMA_NAME}.schema_metadata WHERE key='schema_version'"
        ).fetchone()
        if version and version[0] != str(SCHEMA_VERSION):
            raise RuntimeError(
                "personal_evidence schema v1 cannot be migrated without inventing mission-required semantics; materialize this private run into a new database and retain v1 as legacy evidence."
            )
    for statement in DDL_STATEMENTS:
        conn.execute(statement)
    conn.execute(
        f"INSERT INTO {SCHEMA_NAME}.schema_metadata(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        [str(SCHEMA_VERSION)],
    )


__all__ = [
    "DDL_STATEMENTS",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "apply_schema",
    "source_object_identity",
    "upsert_source_object",
]
