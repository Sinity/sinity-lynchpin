"""Outlook source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _daily_activity(*args: Any, **kwargs: Any) -> Any:
    from ..sources.outlook import daily_activity as impl

    return impl(*args, **kwargs)


def add_outlook(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per day with Outlook email activity.

    Outlook is not project-specific; nodes are emitted with project=None and
    are skipped when a project filter is active.
    """
    from ..core.errors import SourceUnavailableError

    # Skip when a project filter is active — Outlook is not project-attributed.
    if selected:
        return

    try:
        rows = _daily_activity(start=start, end=end)
    except SourceUnavailableError:
        return

    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"outlook:comm:{row.date.isoformat()}",
                kind="communication_day",
                source="outlook",
                date=row.date,
                project=None,
                summary=f"Outlook: {row.sent_count} sent, {row.inbox_count} received",
                payload={
                    "date": row.date.isoformat(),
                    "inbox_count": row.inbox_count,
                    "sent_count": row.sent_count,
                    "unique_correspondents": row.unique_correspondents,
                    "channel": "outlook",
                },
                provenance=EvidenceProvenance("outlook", "materialized"),
            )
        )
