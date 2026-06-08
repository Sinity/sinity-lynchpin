"""Materialization status utilities that require substrate access.

These functions were extracted from core/freshness.py because they import from
the substrate layer, which core is not permitted to do per the layering rules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def diagnostic_ledger_status_payload(*, path: Path | None = None) -> dict[str, Any]:
    """Return diagnostic ledger metadata without queue execution state."""
    from lynchpin.core.freshness import latest_receipts

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
    from lynchpin.core.config import get_config
    from lynchpin.substrate.connection import connect, substrate_path, substrate_read_snapshot_path

    try:
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
