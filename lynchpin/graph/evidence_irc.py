"""IRC source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from ..core.project_mentions import projects_mentioned_in_text
from .evidence_projects import include_project


def conversations_in_range(*args: Any, **kwargs: Any) -> Any:
    from ..sources.irc import conversations_in_range as impl

    return impl(*args, **kwargs)


def add_irc(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per IRC conversation that falls in the date range.

    Project attribution is attempted from channel name and the full message
    text.  Conversations with no project mention and an unrestricted selection
    are emitted without a project so they remain visible in the timeline.
    """
    for conv in conversations_in_range(start=start, end=end):
        full_text = " ".join(msg.text for msg in conv.messages)
        # Also scan channel name for project mentions.
        combined_text = f"{conv.channel} {full_text}"
        mentioned = projects_mentioned_in_text(combined_text)

        projects_to_emit: tuple[str | None, ...]
        if mentioned:
            projects_to_emit = tuple(
                p for p in mentioned if include_project(p, selected)
            )
        else:
            projects_to_emit = (None,) if include_project(None, selected) else ()

        for project in projects_to_emit:
            proj_tag = project or "none"
            node_id = (
                f"irc:{conv.conversation_id}:{conv.channel}:{proj_tag}"
            )
            snippet = full_text[:200] if full_text else f"[{conv.channel}] {conv.total_lines} lines"
            nodes.append(
                EvidenceNode(
                    id=node_id,
                    kind="irc_conversation",
                    source="irc",
                    date=logical_date(conv.start),
                    project=project,
                    start=conv.start,
                    end=conv.end,
                    summary=f"{conv.channel}: {snippet}",
                    payload={
                        "conversation_id": conv.conversation_id,
                        "channel": conv.channel,
                        "total_lines": conv.total_lines,
                        "sinity_lines": conv.sinity_lines,
                        "mention_lines": conv.mention_lines,
                        "source_path": conv.source_path,
                    },
                    provenance=EvidenceProvenance(
                        "irc", "materialized", path=conv.source_path
                    ),
                )
            )
