"""Bounded owner-native execution and activation snapshots for Chisel."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def execution_snapshot(*, days: int, limit: int = 1000, path: Path | None = None) -> dict[str, Any]:
    from . import xtask_history as owner

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    snapshot: dict[str, Any] = {"owner": "sinex", "interface": "xtask_history",
        "observed_at": now.isoformat(), "start": start.isoformat(), "end": now.isoformat(),
        "limit_per_table": limit, "coverage": "unavailable", "gaps": [], "records": {}}
    try:
        target = owner.xtask_history_path(path)
        with sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            columns = owner._table_columns(connection, "invocations")
            invocations = connection.execute(
                f"SELECT {owner._select_list(columns, owner._INVOCATION_COLUMNS)} FROM invocations "
                "WHERE started_at >= ? AND started_at < ? ORDER BY started_at DESC, id DESC LIMIT ?",
                (start.isoformat(), now.isoformat(), limit)).fetchall()
            snapshot["records"]["invocations"] = [asdict(owner._row_to_invocation(r, source_prefix="xtask")) for r in invocations]
            ids = [r["id"] for r in invocations]
            for table, fields, convert in (("stage_timings", owner._STAGE_COLUMNS, owner._row_to_stage_timing),
                                          ("test_results", owner._TEST_RESULT_COLUMNS, owner._row_to_test_result)):
                if not owner._table_exists(connection, table):
                    snapshot["gaps"].append(f"{table} unavailable")
                    continue
                columns = owner._table_columns(connection, table)
                # The converter needs the invocation clock, retained from this same transaction.
                records = connection.execute(
                    f"SELECT {owner._select_list(columns, fields, prefix='r')}, i.started_at AS invocation_started_at "
                    f"FROM {table} r JOIN invocations i ON i.id = r.invocation_id "
                    f"WHERE r.invocation_id IN ({','.join('?' for _ in ids) or 'NULL'}) "
                    "ORDER BY i.started_at DESC, r.id DESC LIMIT ?", [*ids, limit]).fetchall()
                snapshot["records"][table] = [asdict(convert(r, source_prefix="xtask")) for r in records]
            snapshot["coverage"] = "bounded_owner_transaction"
            if any(len(rows) == limit for rows in snapshot["records"].values()):
                snapshot["gaps"].append("row limit reached")
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        snapshot["gaps"].append(str(exc))
    serialized = json.dumps(snapshot["records"], sort_keys=True, default=str)
    snapshot["observation_sha256"] = hashlib.sha256(serialized.encode()).hexdigest()
    snapshot["measurement_rules"] = "Retain owner field units and avg/max/final labels; do not compare unlike workload or environment."
    return snapshot


def activation_snapshot(*, days: int) -> dict[str, Any]:
    from .sinnix_generations import generation_records

    now = datetime.now(timezone.utc)
    records = list(generation_records(start=(now - timedelta(days=days)).date()))
    rows = [asdict(row) for row in records]
    last = max(records, key=lambda row: row.activated_at) if records else None
    return {"owner": "sinnix_generation_log", "observed_at": now.isoformat(),
            "coverage": "recorded_activations" if records else "unavailable",
            "records": rows, "last_activated": asdict(last) if last else None,
            "installed": None, "running": None,
            "gaps": ["Activation history does not attest current installed or running state."]}


def revision_checks(repo: Path, slug: str, revisions: list[str]) -> dict[str, Any]:
    records, gaps = [], []
    for revision in dict.fromkeys(revisions):
        for kind, suffix in (("checks", "check-runs"), ("statuses", "status")):
            try:
                result = subprocess.run(["gh", "api", f"repos/{slug}/commits/{revision}/{suffix}?per_page=100"],
                    cwd=repo, check=True, capture_output=True, text=True, timeout=60)
                payload = json.loads(result.stdout)
                values = payload.get("check_runs" if kind == "checks" else "statuses", [])
                records.extend({"revision": revision, "kind": kind, "owner_record": row} for row in values)
                if payload.get("total_count", len(values)) > len(values):
                    gaps.append(f"{revision}/{kind}: first 100 only")
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                gaps.append(f"{revision}/{kind}: {exc}")
    return {"owner": "github", "observed_at": datetime.now(timezone.utc).isoformat(),
            "records": records, "gaps": gaps, "coverage": "partial" if gaps else "selected_revisions"}
