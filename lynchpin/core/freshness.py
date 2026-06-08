"""Diagnostic ledger for historic materialization decisions.

Normal Lynchpin reads use direct materialization and substrate status. This
module only preserves a small read/write ledger for explaining old decisions
and dependency edges; it does not own a refresh queue or worker.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from lynchpin.core.config import get_config

FreshnessDecisionKind = Literal[
    "fresh",
    "refresh_sync",
    "snapshot_enqueue",
    "coverage_bound",
    "unrefreshable",
    "blocked",
    "failed",
]


@dataclass(frozen=True)
class FreshnessReceipt:
    """Auditable result of one historic materialization/freshness decision."""

    receipt_id: str
    target: str
    decision: FreshnessDecisionKind
    caller: str
    reason: str
    requested_start: str | None = None
    requested_end: str | None = None
    snapshot_refresh_id: str | None = None
    queued_job_id: str | None = None
    artifact_paths: tuple[str, ...] = ()
    artifact_statuses: tuple[dict[str, Any], ...] = ()
    created_at_utc: str = ""
    elapsed_ms: int = 0
    caveats: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_paths"] = list(self.artifact_paths)
        payload["artifact_statuses"] = list(self.artifact_statuses)
        payload["caveats"] = list(self.caveats)
        return payload


def freshness_root() -> Path:
    root = get_config().local_root / "refresh"
    root.mkdir(parents=True, exist_ok=True)
    return root


def freshness_ledger_path() -> Path:
    return freshness_root() / "refresh.sqlite"


def connect_ledger(path: Path | None = None) -> sqlite3.Connection:
    target = path or freshness_ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    return conn


def record_receipt(receipt: FreshnessReceipt, *, path: Path | None = None) -> None:
    with connect_ledger(path) as conn:
        conn.execute(
            """
            INSERT INTO freshness_decision
            (receipt_id, target, decision, caller, reason, requested_start,
             requested_end, snapshot_refresh_id, queued_job_id, created_at_utc,
             elapsed_ms, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, json(?))
            """,
            [
                receipt.receipt_id,
                receipt.target,
                receipt.decision,
                receipt.caller,
                receipt.reason,
                receipt.requested_start,
                receipt.requested_end,
                receipt.snapshot_refresh_id,
                receipt.queued_job_id,
                receipt.created_at_utc or datetime.now(timezone.utc).isoformat(),
                receipt.elapsed_ms,
                _json_dumps(receipt.to_json()),
            ],
        )


def latest_receipts(
    *,
    limit: int = 20,
    target: str | None = None,
    decision: str | None = None,
    include_payload: bool = False,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    filters = []
    params: list[Any] = []
    if target is not None:
        filters.append("target = ?")
        params.append(target)
    if decision is not None:
        filters.append("decision = ?")
        params.append(decision)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    with connect_ledger(path) as conn:
        rows = conn.execute(
            f"""
            SELECT receipt_id, target, decision, caller, reason,
                   snapshot_refresh_id, queued_job_id, created_at_utc, elapsed_ms,
                   payload_json
            FROM freshness_decision
            {where}
            ORDER BY created_at_utc DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        payload_text = item.pop("payload_json", None)
        if include_payload and isinstance(payload_text, str):
            item["payload"] = _json_loads(payload_text)
        result.append(item)
    return result


def record_dependency(
    receipt_id: str,
    *,
    target: str,
    depends_on: str,
    reason: str,
    path: Path | None = None,
) -> None:
    with connect_ledger(path) as conn:
        conn.execute(
            """
            INSERT INTO freshness_dependency
            (receipt_id, target, depends_on, reason, created_at_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                receipt_id,
                target,
                depends_on,
                reason,
                datetime.now(timezone.utc).isoformat(),
            ],
        )


def freshness_dependencies(
    *,
    target: str | None = None,
    receipt_id: str | None = None,
    limit: int = 50,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    filters = []
    params: list[Any] = []
    if target is not None:
        filters.append("target = ?")
        params.append(target)
    if receipt_id is not None:
        filters.append("receipt_id = ?")
        params.append(receipt_id)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    with connect_ledger(path) as conn:
        rows = conn.execute(
            f"""
            SELECT receipt_id, target, depends_on, reason, created_at_utc
            FROM freshness_dependency
            {where}
            ORDER BY created_at_utc DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def freshness_explain_target(
    target: str,
    *,
    limit: int = 20,
    path: Path | None = None,
) -> dict[str, Any]:
    return {
        "target": target,
        "receipts": latest_receipts(limit=limit, target=target, include_payload=True, path=path),
        "dependencies": freshness_dependencies(target=target, limit=limit, path=path),
    }


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS freshness_decision (
            receipt_id          TEXT PRIMARY KEY,
            target              TEXT NOT NULL,
            decision            TEXT NOT NULL,
            caller              TEXT NOT NULL,
            reason              TEXT NOT NULL,
            requested_start     TEXT,
            requested_end       TEXT,
            snapshot_refresh_id TEXT,
            queued_job_id       TEXT,
            created_at_utc      TEXT NOT NULL,
            elapsed_ms          INTEGER NOT NULL DEFAULT 0,
            payload_json        TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS freshness_dependency (
            receipt_id      TEXT NOT NULL,
            target          TEXT NOT NULL,
            depends_on      TEXT NOT NULL,
            reason          TEXT NOT NULL,
            created_at_utc  TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS freshness_decision_target ON freshness_decision(target)")
    conn.execute("CREATE INDEX IF NOT EXISTS freshness_dependency_target ON freshness_dependency(target)")


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _json_loads(value: str) -> dict[str, Any]:
    import json

    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


__all__ = [
    "FreshnessReceipt",
    "connect_ledger",
    "freshness_dependencies",
    "freshness_explain_target",
    "freshness_ledger_path",
    "latest_receipts",
    "record_dependency",
    "record_receipt",
]
