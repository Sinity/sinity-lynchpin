"""Bind completed work-observation attempts to owner inputs and reader code."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def binding_key(refresh_id: str) -> str:
    return f"work_observations:input_binding:{refresh_id}"


def _file_observation(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"path": str(path), "state": "missing"}
    return {
        "path": str(path), "state": "present", "device": stat.st_dev,
        "inode": stat.st_ino, "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
    }


def current_work_input_binding() -> dict[str, str] | None:
    """Return a conservative change detector, never a claim of byte continuity.

    SQLite main/WAL observations detect native ledger changes without scanning
    its history. AgentCTL is read only through its public observation route.
    Failure to inspect a configured input forbids a freshness skip.
    """
    from lynchpin.sources.agentctl import AgentctlObservationError, read_observation_snapshot
    from lynchpin.sources.xtask_history import xtask_history_path

    try:
        ledger = xtask_history_path()
        inputs = {
            "xtask": [_file_observation(path) for path in (ledger, Path(f"{ledger}-wal"))],
            "agentctl": sorted(
                (row.source_id, row.source_revision)
                for row in read_observation_snapshot().observations
            ),
        }
        implementation = _implementation_digest()
    except (OSError, AgentctlObservationError, AttributeError, TypeError, ValueError):
        return None
    return {
        "inputs": hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
        "implementation": implementation,
    }


def _implementation_digest(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[2]
    implementation = hashlib.sha256()
    # Contents and relative names match between a checkout and its installed
    # package. Binding paths would invalidate every declared-job publication.
    for path in sorted(root.rglob("*.py")):
        implementation.update(str(path.relative_to(root)).encode())
        implementation.update(path.read_bytes())
    return implementation.hexdigest()
