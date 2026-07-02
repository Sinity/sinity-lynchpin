"""Machine telemetry and machine-analysis MCP tools.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.

This module is a thin shim. All tools live in:
  machine_status.py       — service state, metrics, health, telemetry, windows
  machine_benchmarks.py   — benchmarks, validation, matched, attribution candidates
  machine_diagnostics.py  — assumption checks, mechanism hypotheses, attribution
  machine_observations.py — work observations, feature frames, mining, observational
Cross-group composites (machine_gaps, machine_dataset) are defined here.
"""

from typing import Any

from lynchpin.mcp.tools._machine_helpers import _best_refresh_or_none  # noqa: F401 - aggregate facade export
from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,  # noqa: F401 - aggregate facade export
    ensure_substrate_materialized_for_read,  # noqa: F401 - aggregate facade export
    json_safe as _json_safe,  # noqa: F401 - aggregate facade export
)

# Import submodules so this private aggregate facade exposes the machine helper surface.
from lynchpin.mcp.tools import machine_status as _machine_status  # noqa: F401
from lynchpin.mcp.tools import machine_benchmarks as _machine_benchmarks  # noqa: F401
from lynchpin.mcp.tools import machine_diagnostics as _machine_diagnostics  # noqa: F401
from lynchpin.mcp.tools import machine_observations as _machine_observations  # noqa: F401
from lynchpin.mcp.tools import machine_workloads as _machine_workloads  # noqa: F401

# Re-export helper names for internal tests and cross-module helper indirection.
from lynchpin.mcp.tools.machine_status import *  # noqa: F401, F403
from lynchpin.mcp.tools.machine_benchmarks import *  # noqa: F401, F403
from lynchpin.mcp.tools.machine_diagnostics import *  # noqa: F401, F403
from lynchpin.mcp.tools.machine_observations import *  # noqa: F401, F403
from lynchpin.mcp.tools.machine_workloads import *  # noqa: F401, F403

# Cross-group composites: call helpers from both status and diagnostics.
from lynchpin.mcp.tools.machine_status import machine_gap_summary, machine_dataset_inventory
from lynchpin.mcp.tools.machine_diagnostics import machine_instrumentation_gaps, machine_dataset_diagnostics


def machine_gaps(
    view: str = "summary",
    threshold_pct: float | None = None,
    limit: int = 50,
    project: str | None = None,
    source: str | None = None,
) -> Any:
    """Machine gap and instrumentation data. view: summary (gap summary), instrumentation (instrumentation gaps)."""
    if view == "summary":
        return machine_gap_summary(threshold_pct=threshold_pct)
    if view == "instrumentation":
        return machine_instrumentation_gaps(limit=limit, project=project, source=source)
    return {"error": f"unknown view {view!r}. choices: summary, instrumentation"}


def machine_dataset(
    view: str = "inventory",
    project: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> Any:
    """Machine dataset data. view: inventory (dataset inventory by project), diagnostics (dataset diagnostic details)."""
    if view == "inventory":
        return machine_dataset_inventory(project=project, start=start, end=end)
    if view == "diagnostics":
        return machine_dataset_diagnostics(kind=kind, severity=severity)
    return {"error": f"unknown view {view!r}. choices: inventory, diagnostics"}
