"""Structured campaign inputs from the public AgentCTL batch read route."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import subprocess
from typing import Any


def revision(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def read_batches(
    project: str,
    *,
    loader: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any]:
    """Read retained batches. Retention does not establish complete attempt history."""
    command = ["agentctl", "batch", "list", "--project", project, "--json"]
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        rows = (loader or _read_json)(command)
        if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("run_id"), str)
            or row.get("project") != project
            or not isinstance(row.get("workers"), list)
            for row in rows
        ):
            raise ValueError("AgentCTL batch list returned an invalid project snapshot")
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {
            "owner": "agentctl",
            "interface": "agentctl.batch.list",
            "rows": [],
            "revision": None,
            "observed_at": observed_at,
            "watermark": None,
            "coverage": "unavailable",
            "gaps": [str(error)],
        }
    return {
        "owner": "agentctl",
        "interface": "agentctl.batch.list",
        "rows": rows,
        "revision": revision(rows),
        "revision_kind": "observation_digest",
        "observed_at": observed_at,
        "watermark": None,
        "coverage": "retained_records",
        "gaps": [
            "Retained batch records do not establish complete attempt history; absent attempts remain unknown"
        ],
    }


def _read_json(command: list[str]) -> Any:
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=30
    )
    return json.loads(result.stdout)


def read_session_evidence(
    session_refs: list[str], *, client: Any = None
) -> dict[str, Any]:
    """Consume Polylogue's stable orchestration product for explicitly linked sessions."""
    result: dict[str, Any] = {
        "owner": "polylogue",
        "interface": "SyncPolylogue.get_session_orchestration",
        "revision": None,
        "revision_kind": "observation_digest",
        "observed_at": None,
        "watermark": None,
        "coverage": "unavailable",
        "gaps": [],
        "sessions": [],
    }
    refs = list(dict.fromkeys(session_refs))
    if not refs:
        result["gaps"].append(
            "Runtime records do not link canonical Polylogue session references"
        )
        return result
    if len(refs) > 50:
        result["gaps"].append(
            "Session evidence is bounded to the first 50 explicit references"
        )
    try:
        if client is None:
            from lynchpin.sources.polylogue_client import _polylogue_client

            client = _polylogue_client()
        for ref in refs[:50]:
            if not ref.startswith("session:") or not ref.removeprefix("session:"):
                result["gaps"].append(f"Unsupported session reference: {ref}")
                continue
            payload = client.get_session_orchestration(ref.removeprefix("session:"))
            if payload is None:
                result["gaps"].append(f"No archive evidence for {ref}")
                continue
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(mode="json")
            if not isinstance(payload, dict):
                raise ValueError(
                    "Polylogue orchestration product is not a structured object"
                )
            result["sessions"].append({"ref": ref, "evidence": payload})
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as error:
        result["gaps"].append(
            f"Polylogue stable orchestration product unavailable: {error}"
        )
    if result["sessions"]:
        result["coverage"] = "explicit_sessions"
        result["revision"] = revision(result["sessions"])
    return result
