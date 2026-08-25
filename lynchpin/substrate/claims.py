"""Analysis-claim promotion and read helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Iterable

from lynchpin.substrate.snapshots import best_materialized_refresh_id

if TYPE_CHECKING:
    import duckdb


@dataclass(frozen=True)
class AnalysisClaimRow:
    claim_id: str
    claim_type: str
    project: str | None
    date: date | None
    support_level: str | None
    confidence: float
    score: float
    summary: str
    source_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    caveats: tuple[str, ...]
    payload: dict[str, Any]


def promote_analysis_claims(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    claims: Iterable[AnalysisClaimRow],
) -> int:
    rows = list(claims)
    conn.execute("DELETE FROM analysis_claim WHERE refresh_id = ?", [refresh_id])
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO analysis_claim (
            refresh_id, claim_id, claim_type, project, date, support_level,
            confidence, score, summary, source_ids, relation_ids, caveats, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                refresh_id,
                row.claim_id,
                row.claim_type,
                row.project,
                row.date,
                row.support_level,
                row.confidence,
                row.score,
                row.summary,
                list(row.source_ids),
                list(row.relation_ids),
                json.dumps(list(row.caveats)),
                json.dumps(row.payload, sort_keys=True),
            )
            for row in rows
        ],
    )
    return len(rows)


def promote_incremental_analysis_claims(
    conn: "duckdb.DuckDBPyConnection",
    *,
    previous_refresh_id: str,
    refresh_id: str,
    tail_start: date,
    claims: Iterable[AnalysisClaimRow],
) -> int:
    """Copy predecessor claims and replace only the refreshed tail."""
    rows = list(claims)
    replacing_existing_refresh = previous_refresh_id == refresh_id
    conn.execute(
        "CREATE OR REPLACE TEMPORARY TABLE incremental_claim_ids (id VARCHAR PRIMARY KEY)"
    )
    if rows:
        conn.executemany(
            "INSERT INTO incremental_claim_ids VALUES (?)",
            [(row.claim_id,) for row in rows],
        )
    if not replacing_existing_refresh:
        conn.execute("DELETE FROM analysis_claim WHERE refresh_id = ?", [refresh_id])
    else:
        conn.execute(
            "DELETE FROM analysis_claim WHERE refresh_id = ? AND date >= ?",
            [refresh_id, tail_start],
        )
        if rows:
            conn.execute(
                "DELETE FROM analysis_claim WHERE refresh_id = ? AND claim_id IN (SELECT id FROM incremental_claim_ids)",
                [refresh_id],
            )
    conn.execute("BEGIN TRANSACTION")
    try:
        if not replacing_existing_refresh:
            conn.execute(
                """
                INSERT INTO analysis_claim (
                    refresh_id, claim_id, claim_type, project, date, support_level,
                    confidence, score, summary, source_ids, relation_ids, caveats, payload
                )
                SELECT ?, claim_id, claim_type, project, date, support_level,
                       confidence, score, summary, source_ids, relation_ids, caveats, payload
                FROM analysis_claim
                WHERE refresh_id = ?
                  AND (date IS NULL OR date < ?)
                  AND claim_id NOT IN (SELECT id FROM incremental_claim_ids)
                """,
                [refresh_id, previous_refresh_id, tail_start],
            )
        if rows:
            conn.executemany(
                """
                INSERT INTO analysis_claim (
                    refresh_id, claim_id, claim_type, project, date, support_level,
                    confidence, score, summary, source_ids, relation_ids, caveats, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        refresh_id,
                        row.claim_id,
                        row.claim_type,
                        row.project,
                        row.date,
                        row.support_level,
                        row.confidence,
                        row.score,
                        row.summary,
                        list(row.source_ids),
                        list(row.relation_ids),
                        json.dumps(list(row.caveats)),
                        json.dumps(row.payload, sort_keys=True),
                    )
                    for row in rows
                ],
            )
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM analysis_claim WHERE refresh_id = ?", [refresh_id]
            ).fetchone()[0]
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return count


def claim_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"claim:{digest}"


def load_analysis_claims(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    project: str | None = None,
    start: date | None = None,
    end: date | None = None,
    claim_type: str | None = None,
    min_confidence: float | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    from lynchpin.substrate.graph import _logical_graph_relation

    clauses: list[str] = []
    params: list[Any] = []
    relation = "analysis_claim"
    if refresh_id is not None:
        relation, params = _logical_graph_relation(
            conn,
            refresh_id=refresh_id,
            table="analysis_claim",
            columns=(
                "refresh_id",
                "claim_id",
                "claim_type",
                "project",
                "date",
                "support_level",
                "confidence",
                "score",
                "summary",
                "source_ids",
                "relation_ids",
                "caveats",
                "payload",
                "materialized_at",
            ),
            key_columns=("claim_id",),
        )
    if project is not None:
        clauses.append("project = ?")
        params.append(project)
    if start is not None:
        clauses.append("date >= ?")
        params.append(start)
    if end is not None:
        clauses.append("date <= ?")
        params.append(end)
    if claim_type is not None:
        clauses.append("claim_type = ?")
        params.append(claim_type)
    if min_confidence is not None:
        clauses.append("confidence >= ?")
        params.append(float(min_confidence))
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(min(max(limit, 1), 10_000))
    rows = conn.execute(
        f"""
        SELECT refresh_id, claim_id, claim_type, project, date, support_level,
               confidence, score, summary, source_ids, relation_ids, caveats, payload,
               materialized_at
        FROM {relation}
        {where}
        ORDER BY confidence DESC, score DESC, date DESC NULLS LAST, project
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_claim_payload(row) for row in rows]


def load_claim_evidence(
    conn: "duckdb.DuckDBPyConnection",
    *,
    claim_id: str,
    refresh_id: str | None = None,
) -> dict[str, Any] | None:
    from lynchpin.substrate.graph import _logical_graph_relation

    params: list[Any] = []
    if refresh_id is not None:
        claim_relation, relation_params = _logical_graph_relation(
            conn,
            refresh_id=refresh_id,
            table="analysis_claim",
            columns=(
                "refresh_id",
                "claim_id",
                "claim_type",
                "project",
                "date",
                "support_level",
                "confidence",
                "score",
                "summary",
                "source_ids",
                "relation_ids",
                "caveats",
                "payload",
                "materialized_at",
            ),
            key_columns=("claim_id",),
        )
        params.extend(relation_params)
    else:
        refresh_id = best_materialized_refresh_id(
            conn,
            "analysis_claim",
            caller="claim_evidence",
        )
        if refresh_id is None:
            return None
        claim_relation, params = _logical_graph_relation(
            conn,
            refresh_id=refresh_id,
            table="analysis_claim",
            columns=(
                "refresh_id",
                "claim_id",
                "claim_type",
                "project",
                "date",
                "support_level",
                "confidence",
                "score",
                "summary",
                "source_ids",
                "relation_ids",
                "caveats",
                "payload",
                "materialized_at",
            ),
            key_columns=("claim_id",),
        )
    params.append(claim_id)
    row = conn.execute(
        f"""
        SELECT refresh_id, claim_id, claim_type, project, date, support_level,
               confidence, score, summary, source_ids, relation_ids, caveats, payload,
               materialized_at
        FROM {claim_relation}
        WHERE claim_id = ?
        """
        + " ORDER BY materialized_at DESC LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    claim = _claim_payload(row)
    relation_ids = claim.get("relation_ids") or []
    source_ids = claim.get("source_ids") or []
    nodes = []
    edges = []
    if source_ids:
        placeholders = ", ".join("?" for _ in source_ids)
        node_relation, node_params = _logical_graph_relation(
            conn,
            refresh_id=refresh_id,
            table="evidence_node",
            columns=("id", "kind", "source", "date", "project", "summary"),
            key_columns=("id",),
        )
        nodes = conn.execute(
            f"SELECT id, kind, source, date, project, summary FROM {node_relation} WHERE id IN ({placeholders})",
            [*node_params, *source_ids],
        ).fetchall()
    if relation_ids:
        placeholders = ", ".join("?" for _ in relation_ids)
        edge_relation, edge_params = _logical_graph_relation(
            conn,
            refresh_id=refresh_id,
            table="evidence_edge",
            columns=("source_id", "target_id", "relation", "evidence", "weight"),
            key_columns=("source_id", "target_id", "relation"),
            cutoff_column=None,
        )
        edges = conn.execute(
            f"SELECT source_id, target_id, relation, evidence, weight FROM {edge_relation} WHERE source_id || '->' || target_id || ':' || relation IN ({placeholders})",
            [*edge_params, *relation_ids],
        ).fetchall()
    claim["evidence_nodes"] = [
        {
            "id": row[0],
            "kind": row[1],
            "source": row[2],
            "date": row[3],
            "project": row[4],
            "summary": row[5],
        }
        for row in nodes
    ]
    claim["evidence_edges"] = [
        {
            "source_id": row[0],
            "target_id": row[1],
            "relation": row[2],
            "evidence": row[3],
            "weight": row[4],
        }
        for row in edges
    ]
    return claim


def _claim_payload(row: Any) -> dict[str, Any]:
    caveats = _json_or_value(row[11], [])
    payload = _json_or_value(row[12], {})
    return {
        "refresh_id": row[0],
        "claim_id": row[1],
        "claim_type": row[2],
        "project": row[3],
        "date": row[4],
        "support_level": row[5],
        "confidence": row[6],
        "score": row[7],
        "summary": row[8],
        "source_ids": tuple(row[9] or ()),
        "relation_ids": tuple(row[10] or ()),
        "caveats": tuple(str(item) for item in caveats),
        "payload": payload if isinstance(payload, dict) else {},
        "materialized_at": row[13],
    }


def _json_or_value(value: object, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


__all__ = [
    "AnalysisClaimRow",
    "claim_id",
    "load_analysis_claims",
    "load_claim_evidence",
    "promote_analysis_claims",
]
