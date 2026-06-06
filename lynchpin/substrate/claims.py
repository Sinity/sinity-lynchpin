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
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
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
        FROM analysis_claim
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
    params: list[Any] = [claim_id]
    refresh_clause = ""
    if refresh_id is not None:
        refresh_clause = " AND refresh_id = ?"
        params.append(refresh_id)
    else:
        refresh_id = best_materialized_refresh_id(
            conn,
            "analysis_claim",
            caller="claim_evidence",
        )
        if refresh_id is None:
            return None
        refresh_clause = " AND refresh_id = ?"
        params.append(refresh_id)
    row = conn.execute(
        """
        SELECT refresh_id, claim_id, claim_type, project, date, support_level,
               confidence, score, summary, source_ids, relation_ids, caveats, payload,
               materialized_at
        FROM analysis_claim
        WHERE claim_id = ?
        """
        + refresh_clause
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
        nodes = conn.execute(
            f"SELECT id, kind, source, date, project, summary FROM evidence_node WHERE refresh_id = ? AND id IN ({placeholders})",
            [claim["refresh_id"], *source_ids],
        ).fetchall()
    if relation_ids:
        placeholders = ", ".join("?" for _ in relation_ids)
        edges = conn.execute(
            f"SELECT source_id, target_id, relation, evidence, weight FROM evidence_edge WHERE refresh_id = ? AND source_id || '->' || target_id || ':' || relation IN ({placeholders})",
            [claim["refresh_id"], *relation_ids],
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
