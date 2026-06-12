"""Arbtt source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _daily_activity(*args: Any, **kwargs: Any) -> Any:
    from ..sources.arbtt import daily_arbtt_activity as impl

    return impl(*args, **kwargs)


def add_arbtt(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per day with Arbtt focus activity.

    Arbtt focus data is not project-specific at this aggregation level;
    nodes are emitted with project=None and are skipped when a project
    filter is active.
    """
    from ..core.errors import SourceUnavailableError

    # Skip when a project filter is active — Arbtt daily aggregates are not
    # project-attributed at this level.
    if selected:
        return

    try:
        rows = _daily_activity(start=start, end=end, ensure=False)
    except (SourceUnavailableError, FileNotFoundError):
        return

    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"arbtt:focus:{row.date.isoformat()}",
                kind="focus_day",
                source="arbtt",
                date=row.date,
                project=None,
                summary=f"Arbtt: {row.event_count} events, {row.active_minutes:.0f}min",
                payload={
                    "date": row.date.isoformat(),
                    "event_count": row.event_count,
                    "active_minutes": round(row.active_minutes, 1),
                    "program_count": row.program_count,
                },
                provenance=EvidenceProvenance("arbtt", "materialized"),
            )
        )
