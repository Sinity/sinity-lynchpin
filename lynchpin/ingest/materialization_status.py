"""Materialization status utilities that require substrate access.

These functions were extracted from core/freshness.py because they import from
the substrate layer, which core is not permitted to do per the layering rules.
"""

from __future__ import annotations

import json
import re
import subprocess
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
    from lynchpin.substrate.connection import substrate_path, substrate_read_snapshot_path

    try:
        canonical = substrate_path()
        snapshot = substrate_read_snapshot_path()
    except Exception:
        duck_dir = get_config().local_root / "duck"
        canonical = duck_dir / "substrate.duckdb"
        snapshot = canonical.with_suffix(".read-snapshot.duckdb")

    latest_materialized_refresh_id, latest_recorded_at, read_error = _latest_materialized_snapshot(canonical)
    (
        latest_available_refresh_id,
        latest_available_recorded_at,
        latest_available_status,
        latest_available_reason,
    ) = _latest_available_snapshot(canonical)
    return {
        "canonical_path": str(canonical),
        "canonical_present": canonical.exists(),
        "canonical_modified_at_utc": _mtime(canonical),
        "snapshot_path": str(snapshot),
        "snapshot_present": snapshot.exists(),
        "snapshot_modified_at_utc": _mtime(snapshot),
        "latest_materialized_refresh_id": latest_materialized_refresh_id,
        "latest_recorded_at": latest_recorded_at,
        "latest_available_refresh_id": latest_available_refresh_id,
        "latest_available_recorded_at": latest_available_recorded_at,
        "latest_available_status": latest_available_status,
        "latest_available_reason": latest_available_reason,
        "status_error": read_error,
    }


def _compact_materialization_snapshot(status: dict[str, Any]) -> dict[str, Any]:
    product_present = bool(status["canonical_present"] or status["snapshot_present"])
    snapshot_id = status.get("latest_materialized_refresh_id")
    available_id = status.get("latest_available_refresh_id")
    ready = bool(product_present and snapshot_id)
    nightly = nightly_materialization_status()
    health = {}
    if ready:
        health = materialization_health(
            recorded_at=status.get("latest_recorded_at"),
            available_status=status.get("latest_available_status"),
            available_reason=status.get("latest_available_reason"),
            nightly=nightly,
        )
        reason = health["reason"]
        materialization_status = health["status"]
    elif status.get("status_error"):
        reason = f"could not inspect substrate promotion snapshot: {status['status_error']}"
        materialization_status = "blocked"
    elif product_present and available_id:
        reason = (
            "substrate has populated tables from latest promotion attempt "
            f"with status {status.get('latest_available_status') or 'unknown'}"
        )
        if status.get("latest_available_reason"):
            reason = f"{reason}: {status['latest_available_reason']}"
        materialization_status = "failed"
    elif product_present:
        reason = "substrate file is present but has no recorded promotion snapshot"
        materialization_status = "blocked"
    else:
        reason = "no substrate product or read snapshot is present"
        materialization_status = "blocked"
    return {
        "status": materialization_status,
        **health,
        "nightly": nightly,
        "primary_product": "evidence_graph_substrate",
        "reason": reason,
        "latest_materialized_refresh_id": snapshot_id,
        "latest_recorded_at": status.get("latest_recorded_at"),
        "latest_available_refresh_id": available_id,
        "latest_available_recorded_at": status.get("latest_available_recorded_at"),
        "latest_available_status": status.get("latest_available_status"),
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


def nightly_materialization_status() -> dict[str, Any]:
    """Read the existing systemd schedule and bounded completion journal."""
    service = "lynchpin-materialize.service"
    timer = "lynchpin-materialize.timer"
    fields = "Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,NextElapseUSecRealtime,LastTriggerUSec"
    try:
        shown = subprocess.run(
            ["systemctl", "show", service, timer, f"--property={fields}"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        units = {}
        for block in shown.stdout.strip().split("\n\n"):
            values = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if values.get("Id"):
                units[values["Id"]] = values
        if units.get(timer, {}).get("LoadState") != "loaded":
            return {"state": "unavailable", "reason": "nightly systemd timer is not installed"}
        payload: dict[str, Any] = {
            "state": units.get(service, {}).get("ActiveState"),
            "service": units.get(service, {}),
            "timer": units.get(timer, {}),
            "latest_completed_run": None,
            "latest_failed_run": None,
        }
        journal = subprocess.run(
            ["journalctl", "-u", service, "--no-pager", "-o", "json", "-n", "20",
             "--grep=job [0-9]+ lynchpin:converge (failed|succeeded)"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        runs = []
        for line in journal.stdout.splitlines():
            try:
                row = json.loads(line)
                match = re.match(r"job (\d+) lynchpin:converge (failed|succeeded)(?: exit (\d+))?(?: |$)", row.get("MESSAGE", ""))
                if match is None:
                    continue
                runs.append({
                    "job_id": int(match[1]), "status": match[2],
                    "exit_code": int(match[3]) if match[3] else 0 if match[2] == "succeeded" else None,
                    "finished_at": datetime.fromtimestamp(
                        int(row["__REALTIME_TIMESTAMP"]) / 1_000_000, tz=timezone.utc,
                    ).isoformat(),
                })
            except (ValueError, KeyError, TypeError):
                continue
        runs.sort(key=lambda run: run["finished_at"], reverse=True)
        payload["latest_completed_run"] = runs[0] if runs else None
        payload["latest_failed_run"] = next((run for run in runs if run["status"] == "failed"), None)
        return payload
    except (OSError, subprocess.SubprocessError) as exc:
        return {"state": "unavailable", "reason": f"nightly status unavailable: {type(exc).__name__}"}


def materialization_health(
    *, recorded_at: Any, available_status: str | None, available_reason: str | None,
    nightly: dict[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    """Keep successful publication, current freshness, and job outcome distinct."""
    observed_at = now or datetime.now(timezone.utc)
    try:
        recorded = datetime.fromisoformat(str(recorded_at))
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
    except ValueError:
        recorded = None
    age_hours = max(0.0, (observed_at - recorded).total_seconds() / 3600) if recorded else None
    freshness = "unknown" if age_hours is None else "stale" if age_hours > 48 else "current"
    reasons = []
    state = "ready"
    if freshness == "stale":
        state = "degraded"
        reasons.append(f"serving promotion is {age_hours:.1f} hours old; nightly freshness is stale")
    elif freshness == "unknown":
        state = "degraded"
        reasons.append("serving promotion time is unknown")
    if available_status and available_status != "ok":
        state = "degraded"
        reasons.append(f"latest promotion status is {available_status}")
        if available_reason:
            reasons.append(available_reason)
    completed = nightly.get("latest_completed_run")
    if isinstance(completed, dict) and completed.get("status") == "failed":
        try:
            failed_at = datetime.fromisoformat(completed["finished_at"])
        except (KeyError, TypeError, ValueError):
            failed_at = None
        if recorded is None or (failed_at is not None and failed_at > recorded):
            state = "failed"
            reasons.append(f"nightly job {completed.get('job_id')} failed after the serving promotion")
    return {
        "status": state,
        "reason": "; ".join(reasons) if reasons else "substrate has a recent successful promotion",
        "serving_freshness": freshness,
        "serving_age_hours": round(age_hours, 2) if age_hours is not None else None,
        "nightly_max_age_hours": 48,
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


def _latest_available_snapshot(canonical: Path) -> tuple[str | None, str | None, str | None, str | None]:
    if not canonical.exists():
        return None, None, None, None
    try:
        from lynchpin.substrate.connection import connect
        from lynchpin.substrate.snapshots import latest_promotion_snapshot

        with connect(canonical, read_only=True) as conn:
            row = latest_promotion_snapshot(conn, caller="compact_materialization_status")
    except Exception:
        return None, None, None, None
    if row is None:
        return None, None, None, None
    refresh_id, recorded_at, status, reason = row
    return (
        str(refresh_id),
        str(recorded_at) if recorded_at is not None else None,
        str(status),
        str(reason) if reason is not None else None,
    )


def _machine_pressure_snapshot() -> dict[str, Any]:
    from lynchpin.core.machine_pressure import machine_pressure_snapshot

    return machine_pressure_snapshot().to_json()


def _mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
