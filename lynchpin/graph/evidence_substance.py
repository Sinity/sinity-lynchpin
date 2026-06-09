"""Substance daily-activity nodes for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _daily_summary(*args: Any, **kwargs: Any) -> Any:
    from ..sources.substance import daily_summary as impl

    return impl(*args, **kwargs)


def add_substance(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per day with substance dose activity.

    Substance data covers 2020-06 → present. Nodes are not project-specific;
    they are skipped when a project filter is active.
    """
    from ..core.errors import SourceUnavailableError

    if selected:
        return

    try:
        rows = _daily_summary(start=start, end=end)
    except SourceUnavailableError:
        return

    for row in rows:
        substances = list(row.substances) if row.substances else []
        nodes.append(
            EvidenceNode(
                id=f"substance:day:{row.date.isoformat()}",
                kind="substance_day",
                source="substance",
                date=row.date,
                project=None,
                summary=(
                    f"Substance: {row.dose_count} dose{'s' if row.dose_count != 1 else ''}"
                    + (f" ({', '.join(substances[:3])})" if substances else "")
                ),
                payload={
                    "date": row.date.isoformat(),
                    "dose_count": row.dose_count,
                    "substances": substances,
                    "total_mg": row.total_mg,
                },
                provenance=EvidenceProvenance("substance", "materialized"),
            )
        )
