"""Raw-log source-node builder for the evidence graph."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from ..core.project_mentions import projects_mentioned_in_text
from .evidence_projects import include_project


_QUOTATION_NOTE = (
    "Quoted material: inclusion in a journal does not establish the journal "
    "owner as its author or independently corroborate its claims."
)


def entries_in_range(*args: Any, **kwargs: Any) -> Any:
    from ..sources.raw_log import entries_in_range as impl

    return impl(*args, **kwargs)


def add_raw_log(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    for entry in entries_in_range(start=start, end=end):
        # Project mentions are associations, not an admission condition for
        # personal evidence. A scoped project query still excludes unassociated
        # entries through include_project(None, selected).
        projects: tuple[str | None, ...] = _projects_from_text(entry.text) or (None,)
        is_quotation = entry.text.lstrip().startswith(">")
        # Keep the existing node IDs compatible with persisted graph edges.
        # This separate source identity survives line shifts and source moves,
        # and groups project projections without counting them as independent
        # corroboration. The source path/line remain locators, not this identity.
        digest = hashlib.sha256(
            f"{entry.timestamp.isoformat()}\0{entry.text}".encode("utf-8")
        ).hexdigest()
        source_entry_id = f"raw-log-entry:{digest}"
        for project in projects:
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
                        "source_entry_id": source_entry_id,
                        "evidence_role": "quotation" if is_quotation else "journal_entry",
                    },
                    provenance=EvidenceProvenance(
                        "raw_log",
                        "materialized",
                        path=entry.source_path,
                        note=_QUOTATION_NOTE if is_quotation else None,
                    ),
                    caveats=(EvidenceCaveat("raw_log", "partial", _QUOTATION_NOTE),)
                    if is_quotation
                    else (),
                )
            )


def _projects_from_text(text: str) -> tuple[str, ...]:
    return projects_mentioned_in_text(text)
