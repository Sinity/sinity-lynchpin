"""Sleep source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _entries_in_range(*args: Any, **kwargs: Any) -> Any:
    from ..sources.sleep import entries_in_range as impl

    return impl(*args, **kwargs)


def add_sleep(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per sleep entry in the date range.

    Sleep is not project-specific; nodes are emitted with project=None and
    are skipped when a project filter is active.
    """
    from ..core.errors import SourceUnavailableError

    # Skip when a project filter is active — sleep is not project-attributed.
    if selected:
        return

    try:
        entries = _entries_in_range(start=start, end=end)
    except SourceUnavailableError:
        return

    for entry in entries:
        total_hours = entry.total_minutes / 60.0
        score_str = f"{entry.avg_score:.0f}" if entry.avg_score is not None else "n/a"
        summary = f"Sleep: {total_hours:.1f}h, score {score_str} ({entry.quality_label})"
        nodes.append(
            EvidenceNode(
                id=f"sleep:{entry.date.isoformat()}",
                kind="sleep_day",
                source="sleep",
                date=entry.date,
                project=None,
                summary=summary,
                payload={
                    "date": entry.date.isoformat(),
                    "total_hours": round(total_hours, 2),
                    "score": entry.avg_score,
                    "quality_label": entry.quality_label,
                    "segments": len(entry.segments),
                },
                provenance=EvidenceProvenance("sleep", "materialized"),
            )
        )
