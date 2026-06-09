"""SMS source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _daily_activity(*args: Any, **kwargs: Any) -> Any:
    from ..sources.sms import daily_activity as impl

    return impl(*args, **kwargs)


def add_sms(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per day with SMS activity.

    SMS is not project-specific; nodes are emitted with project=None and
    are skipped when a project filter is active.
    """
    from ..core.errors import SourceUnavailableError

    # Skip when a project filter is active — SMS is not project-attributed.
    if selected:
        return

    try:
        rows = _daily_activity(start=start, end=end)
    except SourceUnavailableError:
        return

    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"sms:comm:{row.date.isoformat()}",
                kind="communication_day",
                source="sms",
                date=row.date,
                project=None,
                summary=f"SMS: {row.sent_count} sent, {row.received_count} received",
                payload={
                    "date": row.date.isoformat(),
                    "sent_count": row.sent_count,
                    "received_count": row.received_count,
                    "counterpart_count": row.counterpart_count,
                    "channel": "sms",
                },
                provenance=EvidenceProvenance("sms", "materialized"),
            )
        )
