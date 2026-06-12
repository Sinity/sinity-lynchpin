"""IRC source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from ..core.project_mentions import projects_mentioned_in_text
from .evidence_projects import include_project


def conversations_in_range(*args: Any, **kwargs: Any) -> Any:
    from ..sources.irc_raw import extract_conversations as impl

    return impl(*args, **kwargs)


def add_irc(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per IRC conversation that falls in the date range.

    Project attribution is attempted from channel name and the full message
    text.  Conversations with no project mention and an unrestricted selection
    are emitted without a project so they remain visible in the timeline.
    """
    from ..materialization import ensure_materialized

    ensure_materialized(
        "irc",
        window=(start, end + timedelta(days=1)),
        budget="manual",
    )
    for conv in conversations_in_range(start=start, end=end, ensure=False):
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
            node_id = f"irc:{conv.conversation_id}:{conv.channel}:{proj_tag}"
            message_count = int(
                getattr(conv, "message_count", getattr(conv, "total_lines", 0))
            )
            source_files = sorted(
                {
                    str(getattr(msg, "source_file", ""))
                    for msg in getattr(conv, "messages", ())
                    if getattr(msg, "source_file", "")
                }
            )
            snippet = (
                full_text[:200]
                if full_text
                else f"[{conv.channel}] {message_count} messages"
            )
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
                        "total_lines": message_count,
                        "message_count": message_count,
                        "unique_speakers": getattr(conv, "unique_speakers", None),
                        "speakers": list(getattr(conv, "speakers", ())),
                        "source_files": source_files,
                    },
                    provenance=EvidenceProvenance(
                        "irc",
                        "materialized",
                        path=source_files[0] if len(source_files) == 1 else None,
                    ),
                )
            )
