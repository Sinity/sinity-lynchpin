"""Clipboard source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from ..core.project_mentions import projects_mentioned_in_text
from .evidence_projects import include_project


def entries_in_range(*args: Any, **kwargs: Any) -> Any:
    from ..sources.clipboard import entries_in_range as impl

    return impl(*args, **kwargs)


def add_clipboard(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    """Emit one EvidenceNode per clipboard entry that mentions a selected project.

    Entries with no text content are skipped.  For entries with no project
    mention and an empty ``selected`` set (= include all), a single unprojectd
    node is emitted so the activity still surfaces in the timeline.
    """
    for entry in entries_in_range(start=start, end=end):
        text = entry.value
        if not text:
            continue
        mentioned = projects_mentioned_in_text(text)
        # When no project is detected and selection is unrestricted, emit once
        # without a project so the entry is visible.
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
                f"clipboard:{entry.recorded_at.isoformat()}:{proj_tag}:{hash(text) & 0xFFFFFF:06x}"
            )
            nodes.append(
                EvidenceNode(
                    id=node_id,
                    kind="clipboard_entry",
                    source="clipboard",
                    date=logical_date(entry.recorded_at),
                    project=project,
                    start=entry.recorded_at,
                    end=entry.recorded_at,
                    summary=f"[{entry.kind}] {text[:200]}",
                    payload={
                        "kind": entry.kind,
                        "value": text[:1000],
                        "pinned": entry.pinned,
                        "file_path": entry.file_path,
                        "source_file": entry.source,
                    },
                    provenance=EvidenceProvenance(
                        "clipboard", "materialized", path=entry.source
                    ),
                )
            )
