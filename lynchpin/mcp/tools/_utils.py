"""Shared utilities for MCP tool modules.

Keep helpers that are imported by multiple tool files here to avoid
circular coupling between substrate.py and views.py.
"""

from __future__ import annotations

import base64
import logging
from contextvars import ContextVar
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from lynchpin.mcp.registry import PUBLIC_TOOL_NAMES
from lynchpin.substrate import snapshots as _snapshots

PLATFORM_TOOL_NAMES: tuple[str, ...] = PUBLIC_TOOL_NAMES

log = logging.getLogger(__name__)

# Collects non-ready materialization results seen during the current public
# MCP tool call, so lynchpin.mcp.tools.public._internal_call — the single
# choke point every routed action passes through — can surface them as a
# response caveat (lynchpin-zoz) without every one of the ~40 internal
# functions that call ensure_substrate_materialized_for_read needing to
# thread the result through its own return value. None outside of an active
# _internal_call (the list is installed/torn down there); appends are a
# no-op when nothing is collecting, so calling this helper directly in a
# script or test never raises.
_MATERIALIZATION_CAVEATS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "lynchpin_mcp_materialization_caveats", default=None
)


def _record_materialization_caveat(payload: dict[str, Any]) -> None:
    caveats = _MATERIALIZATION_CAVEATS.get()
    if caveats is not None:
        caveats.append(payload)


def json_safe(value: Any) -> Any:
    """Recursively convert a DuckDB result value to a JSON-serialisable type."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    return value


def dataclass_to_json_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance to a JSON-serialisable dict."""
    d = asdict(obj)
    return {k: json_safe(v) for k, v in d.items()}


def half_open_date_window(
    start: date | None,
    end: date | None,
) -> tuple[date, date] | None:
    """Convert public inclusive date bounds to a materialization window."""

    if start is None or end is None:
        return None
    return (start, end + timedelta(days=1))


def ensure_substrate_materialized_for_read(
    *,
    caller: str,
    window: tuple[date, date] | None = None,
) -> dict[str, Any]:
    """Inspect the substrate product before a read.

    Normal unpinned reads may perform one bounded tail convergence through the
    materialization boundary. The materialization layer inspects durable graph
    coverage and source fingerprints, then either reuses the serving generation,
    publishes a bounded candidate, or reports why explicit maintenance is needed.

    Every caller of this function discards its return value directly (it's
    called purely for the check, not the payload) — so when the requested
    window isn't covered by anything promoted, the resulting 'blocked' status
    (evidence_graph_substrate has no registered materializer; only an
    explicit `substrate_snapshot` promote can advance it) previously went
    entirely unobserved and the read proceeded as if nothing were wrong,
    silently returning whatever partial/empty data the query happened to
    produce (lynchpin-zoz). Fixed two ways instead of touching every one of
    those ~40 call sites: this logs a warning immediately, and also records
    the result via ``_record_materialization_caveat`` into a ContextVar that
    ``lynchpin.mcp.tools.public._internal_call`` — the single choke point
    every routed public-tool action passes through — collects and attaches
    to the response's ``meta.materialization_caveats`` before returning.
    """

    from lynchpin.materialization import ensure_materialized

    result = ensure_materialized(
        "evidence_graph_substrate",
        window=window,
        budget="inline",
    )
    if result.status not in ("ready", "updated", "pinned"):
        log.warning(
            "mcp.%s: evidence_graph_substrate is not ready for window=%s "
            "(status=%s reason=%r) — read is proceeding anyway and may "
            "return partial or empty data without saying so",
            caller,
            window,
            result.status,
            result.reason,
        )
        _record_materialization_caveat(
            {
                "caller": caller,
                "window": [d.isoformat() for d in window] if window else None,
                "status": result.status,
                "reason": result.reason,
            }
        )
    payload = result.to_json()
    payload["caller"] = caller
    return payload


def pinned_materialization_for_read(*, caller: str, refresh_id: str) -> dict[str, Any]:
    """Return explanatory materialization metadata for explicit snapshot reads."""
    return {
        "name": "evidence_graph_substrate",
        "status": "pinned",
        "changed": False,
        "caller": caller,
        "refresh_id": refresh_id,
    }


def latest_materialized_refresh_id(
    conn: Any,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> str | None:
    """Return the latest materialized substrate refresh_id."""

    return _snapshots.latest_materialized_refresh_id(
        conn,
        caller=caller,
        ledger_path=ledger_path,
    )


def best_materialized_refresh_id(
    conn: Any,
    table: str,
    *,
    caller: str,
    ledger_path: Path | None = None,
) -> str | None:
    """Return the highest-coverage materialized refresh_id for a table."""

    return _snapshots.best_materialized_refresh_id(
        conn,
        table,
        caller=caller,
        ledger_path=ledger_path,
    )


def require_best_materialized_refresh_id(
    conn: Any,
    table: str,
    *,
    caller: str,
    tool: str,
    ledger_path: Path | None = None,
) -> str:
    """Return the best materialized refresh id or raise when no data exists."""

    return _snapshots.require_best_materialized_refresh_id(
        conn,
        table,
        caller=caller,
        tool=tool,
        ledger_path=ledger_path,
    )


def registered_tool_names() -> tuple[str, ...]:
    """Return names currently registered on the in-process FastMCP app."""
    from lynchpin.mcp.server import app

    tools = getattr(getattr(app, "_tool_manager", None), "_tools", {})
    if not isinstance(tools, dict):
        return ()
    return tuple(sorted(str(name) for name in tools))


def declared_tool_names() -> tuple[str, ...]:
    """Return public MCP tool names expected in the collapsed registry."""

    return tuple(sorted(PUBLIC_TOOL_NAMES))


def mcp_tool_registry_summary() -> dict[str, Any]:
    """Classify live MCP tools into source-declared, platform, and unexpected sets."""

    registered = set(registered_tool_names())
    declared = set(declared_tool_names())
    platform = set(PLATFORM_TOOL_NAMES)
    from lynchpin.mcp.server import app

    tools = getattr(getattr(app, "_tool_manager", None), "_tools", {})
    module_rows: dict[str, dict[str, Any]] = {}
    if isinstance(tools, dict):
        for name in registered:
            tool = tools.get(name)
            fn = getattr(tool, "fn", None)
            module = str(getattr(fn, "__module__", "unknown"))
            row = module_rows.setdefault(
                module,
                {
                    "module": module,
                    "registered_tool_count": 0,
                    "declared_tool_count": 0,
                    "platform_tool_count": 0,
                    "unexpected_unmapped_tool_count": 0,
                    "tools": [],
                },
            )
            row["registered_tool_count"] += 1
            if name in declared:
                row["declared_tool_count"] += 1
            if name in platform:
                row["platform_tool_count"] += 1
            if name not in declared and name not in platform:
                row["unexpected_unmapped_tool_count"] += 1
            row["tools"].append(name)
    module_summary = tuple(
        {
            **row,
            "tools": tuple(sorted(row["tools"])),
        }
        for row in sorted(
            module_rows.values(),
            key=lambda item: (-int(item["registered_tool_count"]), str(item["module"])),
        )
    )
    return {
        "registered": tuple(sorted(registered)),
        "declared": tuple(sorted(declared)),
        "platform": tuple(sorted(platform)),
        "registered_platform": tuple(sorted(registered & platform)),
        "missing_platform": tuple(sorted(platform - registered)),
        "missing_declared": tuple(sorted(declared - registered)),
        "registered_unmapped": tuple(sorted(registered - declared)),
        "unexpected_unmapped": tuple(sorted(registered - declared - platform)),
        "module_summary": module_summary,
    }
