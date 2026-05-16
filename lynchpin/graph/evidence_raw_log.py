"""Raw-log source-node builder for the evidence graph."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from ..core.project_mentions import projects_mentioned_in_text
from .evidence_projects import include_project


def entries_in_range(*args: Any, **kwargs: Any) -> Any:
    from ..sources.raw_log import entries_in_range as impl

    return impl(*args, **kwargs)


def add_raw_log(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    for entry in entries_in_range(start=start, end=end):
        for project in _projects_from_text(entry.text):
            if not include_project(project, selected):
                continue
            nodes.append(
                EvidenceNode(
                    id=f"raw-log:{entry.source_path}:{entry.line_no}:{project}",
                    kind="raw_log",
                    source="raw_log",
                    date=logical_date(entry.timestamp),
                    project=project,
                    start=entry.timestamp,
                    end=entry.timestamp,
                    summary=entry.text[:240],
                    payload={
                        "line_no": entry.line_no,
                        "source_path": entry.source_path,
                        "text": entry.text,
                    },
                    provenance=EvidenceProvenance(
                        "raw_log", "local-fast", path=entry.source_path
                    ),
                )
            )


def _projects_from_text(text: str) -> tuple[str, ...]:
    return projects_mentioned_in_text(text)
