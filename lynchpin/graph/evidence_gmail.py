"""Gmail source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _daily_activity(*args: Any, **kwargs: Any) -> Any:
    from ..sources.gmail_takeout import daily_gmail_activity as impl

    return impl(*args, **kwargs)


def add_gmail(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per day with Gmail activity.

    Gmail is not project-specific; nodes are emitted with project=None and
    are skipped when a project filter is active.
    """
    from ..core.errors import SourceUnavailableError

    if selected:
        return

    try:
        rows = _daily_activity(start=start, end=end, ensure=False)
    except (SourceUnavailableError, Exception):  # noqa: BLE001 - graceful degradation
        return

    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"gmail:comm:{row.date.isoformat()}",
                kind="communication_day",
                source="gmail",
                date=row.date,
                project=None,
                summary=(
                    f"Gmail: {row.message_count} messages, "
                    f"{row.outbound_count} sent, {row.inbound_count} received"
                ),
                payload={
                    "date": row.date.isoformat(),
                    "message_count": row.message_count,
                    "thread_count": row.thread_count,
                    "unique_correspondents": row.unique_correspondents,
                    "outbound_count": row.outbound_count,
                    "inbound_count": row.inbound_count,
                    "channel": "gmail",
                },
                provenance=EvidenceProvenance("gmail", "materialized"),
            )
        )
