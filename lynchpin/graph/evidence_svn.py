"""SVN source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def _daily_activity(*args: Any, **kwargs: Any) -> Any:
    from ..sources.svn import daily_activity as impl

    return impl(*args, **kwargs)


def add_svn(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per day with SVN commit activity.

    SVN data covers the JBR workplace period (2017-07 → 2022-09).
    Nodes are not project-specific; they are skipped when a project filter is
    active that would exclude workplace-era commits.
    """
    from ..core.errors import SourceUnavailableError

    # SVN commits are not attributed to modern personal projects.
    # Emit only when no project filter is active, so workplace-era history
    # appears in full-corpus evidence builds.
    if selected:
        return

    try:
        rows = _daily_activity(start=start, end=end)
    except SourceUnavailableError:
        return

    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"svn:commit:{row.date.isoformat()}",
                kind="svn_commit_day",
                source="svn",
                date=row.date,
                project=None,
                summary=(
                    f"SVN: {row.commit_count} commit{'s' if row.commit_count != 1 else ''}, "
                    f"{row.files_changed} file{'s' if row.files_changed != 1 else ''} changed"
                ),
                payload={
                    "date": row.date.isoformat(),
                    "commit_count": row.commit_count,
                    "files_changed": row.files_changed,
                    "files_added": row.files_added,
                    "files_modified": row.files_modified,
                    "files_deleted": row.files_deleted,
                    "revisions": list(row.revisions),
                },
                provenance=EvidenceProvenance("svn", "materialized"),
            )
        )
