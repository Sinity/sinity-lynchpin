"""Runtime and deployment introspection MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

from lynchpin.mcp.tools._utils import latest_materialized_refresh_id
from lynchpin.mcp.tools._utils import mcp_tool_registry_summary
from lynchpin.mcp.tools._utils import registered_tool_names
from lynchpin.ingest.materialization_status import (
    compact_materialization_status,
    diagnostic_ledger_status_payload,
)
from lynchpin.core.freshness import (
    freshness_dependencies,
    freshness_explain_target,
    latest_receipts,
)


def _git_value(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def mcp_runtime_status() -> dict[str, Any]:
    """Report the MCP server code path, repo revision, and latest materialized substrate ID."""
    from lynchpin.materialization import substrate_materialization_snapshot
    from lynchpin.substrate.connection import connect, substrate_path

    package_root = Path(__file__).resolve().parents[2]
    repo_root_raw = os.environ.get("LYNCHPIN_REPO_ROOT")
    repo_root = Path(repo_root_raw).resolve() if repo_root_raw else None

    git_head = _git_value(repo_root, "rev-parse", "HEAD") if repo_root else None
    git_branch = _git_value(repo_root, "branch", "--show-current") if repo_root else None
    git_status = _git_value(repo_root, "status", "--short") if repo_root else None

    try:
        with connect(substrate_path(), read_only=True) as conn:
            latest_materialized = latest_materialized_refresh_id(conn, caller="mcp_runtime_status")
    except Exception as exc:  # noqa: BLE001 - status tool should report broken substrate access.
        latest_materialized = None
        substrate_error = f"{type(exc).__name__}: {exc}"
    else:
        substrate_error = None

    live_tools = registered_tool_names()
    return {
        "package_root": str(package_root),
        "repo_root": str(repo_root) if repo_root else None,
        "uses_repo_source": bool(repo_root and str(package_root).startswith(str(repo_root))),
        "git": {
            "head": git_head,
            "branch": git_branch,
            "dirty": bool(git_status),
        },
        "mcp": {
            "registered_tool_count": len(live_tools),
        },
        "substrate": {
            "path": str(substrate_path()),
            "latest_materialized_refresh_id": latest_materialized,
            "error": substrate_error,
            "materialization": substrate_materialization_snapshot(
                substrate_path(),
                latest_materialized_refresh_id=latest_materialized,
            ).to_json(),
        },
    }


def mcp_surface_self_check() -> dict[str, Any]:
    """Check contract-declared MCP tools against the live registered tool set."""
    status = mcp_runtime_status()
    registry = mcp_tool_registry_summary()
    missing = list(registry["missing_declared"])
    unexpected_unmapped = list(registry["unexpected_unmapped"])
    return {
        "runtime": status,
        "declared_tool_count": len(registry["declared"]),
        "registered_tool_count": len(registry["registered"]),
        "platform_tool_count": len(registry["platform"]),
        "missing_declared_tools": missing,
        "missing_platform_tools": list(registry["missing_platform"]),
        "registered_platform_tools": list(registry["registered_platform"]),
        "registered_unmapped_tools": list(registry["registered_unmapped"]),
        "unexpected_unmapped_tools": unexpected_unmapped,
        "module_summary": list(registry["module_summary"]),
        "ok": not missing and not unexpected_unmapped,
        "restart_hint": (
            "registered tools differ from source contracts/platform classification; restart the MCP server after code changes"
            if missing or unexpected_unmapped
            else None
        ),
    }


def diagnostic_ledger_status() -> dict[str, Any]:
    """Return diagnostic ledger and exceptional queue state."""

    return diagnostic_ledger_status_payload()


def observability_status() -> dict[str, Any]:
    """Return compact live status for panels and operator prompts."""

    payload = compact_materialization_status()
    machine = payload.get("machine")
    if isinstance(machine, dict):
        # Standing storage guardrails (2026-06-12 overhaul): daily wear vs
        # per-device budget, plus the login-path SQLite-lock canary that only
        # fires under pathological IO starvation. Both degrade to state=error
        # instead of breaking the status surface.
        from lynchpin.analysis.machine.wear import (
            machine_wear_status,
            storage_canary_status,
        )

        machine["wear"] = machine_wear_status()
        machine["storage_canary"] = storage_canary_status()
    return {**payload, "kind": "lynchpin_observability_status"}


def diagnostic_ledger_explain(target: str, limit: int = 20) -> dict[str, Any]:
    """Explain diagnostic ledger decisions and exceptional work for a target."""

    return freshness_explain_target(target, limit=limit)


def diagnostic_ledger_receipts(
    limit: int = 20,
    target: str | None = None,
    decision: str | None = None,
    include_payload: bool = False,
) -> list[dict[str, Any]]:
    """Return recent diagnostic ledger decisions."""

    return latest_receipts(
        limit=limit,
        target=target,
        decision=decision,
        include_payload=include_payload,
    )


def diagnostic_source_materialization_decision(
    source: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Debug a source-contract materialization decision without recording ledger state."""

    from datetime import date, timedelta

    from lynchpin.materialization import ensure_materialized

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) + timedelta(days=1) if end else None
    window = (start_d, end_d) if start_d is not None and end_d is not None else None
    return ensure_materialized(source, window=window).to_json()


def diagnostic_ledger_dependency_edges(
    target: str | None = None,
    receipt_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recorded diagnostic ledger dependency/provenance edges."""

    return freshness_dependencies(target=target, receipt_id=receipt_id, limit=limit)


def mcp_status(view: str = "runtime") -> dict[str, Any]:
    """MCP server introspection. view: runtime (code path/git/substrate state), self_check (contract-declared vs live tools)."""
    if view == "runtime":
        return mcp_runtime_status()
    if view == "self_check":
        return mcp_surface_self_check()
    return {"error": f"unknown view {view!r}. choices: runtime, self_check"}


def diagnostic_ledger(
    view: str = "status",
    target: str | None = None,
    receipt_id: str | None = None,
    decision: str | None = None,
    source: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
    include_payload: bool = False,
) -> Any:
    """Diagnostic ledger and materialization decisions. view: status (ledger+queue state), explain (decisions for target), receipts (recent decisions), edges (dependency/provenance edges), decision (debug source-contract decision)."""
    if view == "status":
        return diagnostic_ledger_status()
    if view == "explain":
        if target is None:
            return {"error": "target is required for view=explain"}
        return diagnostic_ledger_explain(target=target, limit=limit)
    if view == "receipts":
        return diagnostic_ledger_receipts(limit=limit, target=target, decision=decision, include_payload=include_payload)
    if view == "edges":
        return diagnostic_ledger_dependency_edges(target=target, receipt_id=receipt_id, limit=limit)
    if view == "decision":
        if source is None:
            return {"error": "source is required for view=decision"}
        return diagnostic_source_materialization_decision(source=source, start=start, end=end)
    return {"error": f"unknown view {view!r}. choices: status, explain, receipts, edges, decision"}
