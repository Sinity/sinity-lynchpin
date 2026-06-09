"""Reddit source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _daily_activity(*args: Any, **kwargs: Any) -> Any:
    from ..sources.reddit import daily_activity as impl

    return impl(*args, **kwargs)


def add_reddit(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per day with Reddit activity.

    Reddit is not project-specific; nodes are emitted with project=None and
    are skipped when a project filter is active.
    """
    from ..core.errors import SourceUnavailableError

    # Skip when a project filter is active — Reddit is not project-attributed.
    if selected:
        return

    try:
        rows = _daily_activity(start=start, end=end)
    except SourceUnavailableError:
        return

    for row in rows:
        subs_str = ", ".join(row.top_subreddits) if row.top_subreddits else ""
        summary = f"reddit: {row.post_count} posts, {row.comment_count} comments"
        if subs_str:
            summary += f" ({subs_str})"
        nodes.append(
            EvidenceNode(
                id=f"reddit:social:{row.date.isoformat()}",
                kind="social_day",
                source="reddit",
                date=row.date,
                project=None,
                summary=summary,
                payload={
                    "date": row.date.isoformat(),
                    "comment_count": row.comment_count,
                    "post_count": row.post_count,
                    "top_subreddits": list(row.top_subreddits),
                    "total_words": row.total_words,
                },
                provenance=EvidenceProvenance("reddit", "materialized"),
            )
        )
