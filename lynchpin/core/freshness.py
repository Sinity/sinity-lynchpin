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


def diagnostic_ledger_status_payload(*, path: Path | None = None) -> dict[str, Any]:
    """Return diagnostic ledger metadata without queue execution state."""

    status = _substrate_product_status()
    return {
        **status,
        "latest_receipts": latest_receipts(limit=5, path=path),
    }


def compact_materialization_status() -> dict[str, Any]:
    """Small stable materialization status payload for panels and live prompts."""

    status = _substrate_product_status()
    machine = _machine_pressure_snapshot()
    materialization = _compact_materialization_snapshot(status)
    return {
        "kind": "lynchpin_materialization_status",
        "health": "attention" if materialization["status"] != "ready" else "ok",
        "materialization": materialization,
        "substrate": {
            "canonical_present": status["canonical_present"],
            "snapshot_present": status["snapshot_present"],
            "snapshot_modified_at_utc": status["snapshot_modified_at_utc"],
        },
        "machine": machine,
    }


def _substrate_product_status() -> dict[str, Any]:
    """Return cheap substrate product status without touching the ledger."""

    try:
        from lynchpin.substrate.connection import substrate_path, substrate_read_snapshot_path

        canonical = substrate_path()
        snapshot = substrate_read_snapshot_path()
    except Exception:
        duck_dir = get_config().local_root / "duck"
        canonical = duck_dir / "substrate.duckdb"
        snapshot = canonical.with_suffix(".read-snapshot.duckdb")
    latest_materialized_refresh_id, latest_recorded_at, read_error = _latest_materialized_snapshot(canonical)
    return {
        "canonical_path": str(canonical),
        "canonical_present": canonical.exists(),
        "canonical_modified_at_utc": _mtime(canonical),
        "snapshot_path": str(snapshot),
        "snapshot_present": snapshot.exists(),
        "snapshot_modified_at_utc": _mtime(snapshot),
        "latest_materialized_refresh_id": latest_materialized_refresh_id,
        "latest_recorded_at": latest_recorded_at,
        "status_error": read_error,
    }


def _compact_materialization_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    product_present = bool(status["canonical_present"] or status["snapshot_present"])
    snapshot_id = status.get("latest_materialized_refresh_id")
    ready = bool(product_present and snapshot_id)
    if ready:
        reason = "substrate has a recorded promotion snapshot"
    elif status.get("status_error"):
        reason = f"could not inspect substrate promotion snapshot: {status['status_error']}"
    elif product_present:
        reason = "substrate file is present but has no recorded promotion snapshot"
    else:
        reason = "no substrate product or read snapshot is present"
    return {
        "status": "ready" if ready else "blocked",
        "primary_product": "evidence_graph_substrate",
        "reason": reason,
        "latest_materialized_refresh_id": snapshot_id,
        "latest_recorded_at": status.get("latest_recorded_at"),
        "products": {
            "evidence_graph_substrate": {
                "status": "ready" if status["canonical_present"] else "blocked",
                "path": status["canonical_path"],
                "modified_at_utc": status["canonical_modified_at_utc"],
            },
            "substrate_read_snapshot": {
                "status": "ready" if status["snapshot_present"] else "blocked",
                "path": status["snapshot_path"],
                "modified_at_utc": status["snapshot_modified_at_utc"],
            },
        },
    }


def _latest_materialized_snapshot(canonical: Path) -> tuple[str | None, str | None, str | None]:
    if not canonical.exists():
        return None, None, None
    try:
        from lynchpin.substrate.connection import connect
        from lynchpin.substrate.snapshots import latest_materialized_snapshot

        with connect(canonical, read_only=True) as conn:
            row = latest_materialized_snapshot(conn, caller="compact_materialization_status")
    except Exception as exc:  # noqa: BLE001 - status should report substrate read failures.
        return None, None, f"{type(exc).__name__}: {exc}"
    if row is None:
        return None, None, None
    refresh_id, recorded_at = row
    return str(refresh_id), str(recorded_at) if recorded_at is not None else None, None


def _machine_pressure_snapshot() -> dict[str, Any]:
    from lynchpin.core.machine_pressure import machine_pressure_snapshot

    return machine_pressure_snapshot().to_json()


def _mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


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
    "compact_materialization_status",
    "connect_ledger",
    "diagnostic_ledger_status_payload",
    "freshness_dependencies",
    "freshness_explain_target",
    "freshness_ledger_path",
    "latest_receipts",
    "record_dependency",
    "record_receipt",
]
