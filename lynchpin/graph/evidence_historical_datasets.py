"""Evidence nodes for historical datasets."""

from __future__ import annotations

from datetime import date

from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..sources.historical_datasets import source_summary


def add_historical_datasets(
    nodes: list[EvidenceNode],
    *,
    start: date,
    end: date,
) -> None:
    for item in source_summary():
        if item.last_date is not None and item.last_date < start:
            continue
        if item.first_date is not None and item.first_date > end:
            continue
        node_date = item.last_date or end
        nodes.append(
            EvidenceNode(
                id=f"historical:{item.source}",
                kind="historical_dataset",
                source=item.source,
                date=node_date,
                project=None,
                summary=f"{item.source}: {item.count} rows",
                payload={
                    "source": item.source,
                    "path": item.path,
                    "count": item.count,
                    "first_date": item.first_date.isoformat() if item.first_date else None,
                    "last_date": item.last_date.isoformat() if item.last_date else None,
                },
                provenance=EvidenceProvenance(item.source, "local-fast"),
                caveats=(
                    EvidenceCaveat(
                        item.source,
                        "partial",
                        "Historical dataset is parsed at inventory/source level; deeper semantic extraction varies by dataset.",
                    ),
                ),
            )
        )


__all__ = ["add_historical_datasets"]
