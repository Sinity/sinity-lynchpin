"""Readiness gate for recent operator retrospective explanations.

This is deliberately a gate, not a narrative generator. It decides whether
Lynchpin has enough recent source coverage to support behavioral explanations
of velocity changes, or whether callers should restrict themselves to
structural/git-only claims until required products are materialized.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from lynchpin.materialization import (
    MaterializedDataset,
    audit_materialization,
    materialized_dataset_coverage,
)

CORE_BEHAVIORAL_SOURCES = ("activitywatch", "atuin", "machine", "xtask_history")
CONTEXT_SOURCES = ("webhistory", "irc", "polylogue_devtools", "substance")
POLYLOGUE_SOURCE = "polylogue"


@dataclass(frozen=True)
class RetrospectiveSourceReadiness:
    source: str
    role: str
    status: str
    relation: str
    row_count: int | None
    first_date: date | None
    last_date: date | None
    blocking: bool
    reason: str
    materialization_hint: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["first_date"] = self.first_date.isoformat() if self.first_date else None
        payload["last_date"] = self.last_date.isoformat() if self.last_date else None
        return payload


@dataclass(frozen=True)
class OperatorRetrospectiveReadiness:
    start: date
    end: date
    generated_at_utc: str
    mode: str
    trustworthy: bool
    behavioral_explanation_allowed: bool
    structural_explanation_allowed: bool
    blocking_sources: tuple[str, ...]
    caveats: tuple[str, ...]
    sources: tuple[RetrospectiveSourceReadiness, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "generated_at_utc": self.generated_at_utc,
            "mode": self.mode,
            "trustworthy": self.trustworthy,
            "behavioral_explanation_allowed": self.behavioral_explanation_allowed,
            "structural_explanation_allowed": self.structural_explanation_allowed,
            "blocking_sources": list(self.blocking_sources),
            "caveats": list(self.caveats),
            "sources": [row.to_dict() for row in self.sources],
        }


def operator_retrospective_readiness(
    *,
    start: date,
    end: date,
    require_polylogue: bool = False,
    datasets: Iterable[MaterializedDataset] | None = None,
) -> OperatorRetrospectiveReadiness:
    """Classify whether a recent retrospective can make behavioral claims.

    `end` follows the repository coverage convention: exclusive upper bound.
    Polylogue is a caveat by default because AI chat insight products are a
    known independent repair lane; callers can opt into requiring it when AI
    semantics are central to the question.
    """
    dataset_map = {row.name: row for row in (datasets or audit_materialization())}
    rows: list[RetrospectiveSourceReadiness] = []

    for source in CORE_BEHAVIORAL_SOURCES:
        rows.append(_source_row(dataset_map, source, role="core_behavioral", start=start, end=end, blocking=True))
    for source in CONTEXT_SOURCES:
        rows.append(_source_row(dataset_map, source, role="context", start=start, end=end, blocking=False))

    polylogue = _source_row(
        dataset_map,
        POLYLOGUE_SOURCE,
        role="ai_semantics",
        start=start,
        end=end,
        blocking=require_polylogue,
    )
    rows.append(polylogue)

    blocking = tuple(row.source for row in rows if row.blocking and row.status != "ready")
    caveats = _caveats(rows=rows, require_polylogue=require_polylogue)
    behavioral_allowed = not blocking
    mode = "behavioral" if behavioral_allowed else "structural_only"
    return OperatorRetrospectiveReadiness(
        start=start,
        end=end,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        trustworthy=behavioral_allowed,
        behavioral_explanation_allowed=behavioral_allowed,
        structural_explanation_allowed=True,
        blocking_sources=blocking,
        caveats=caveats,
        sources=tuple(rows),
    )


def _source_row(
    dataset_map: dict[str, MaterializedDataset],
    source: str,
    *,
    role: str,
    start: date,
    end: date,
    blocking: bool,
) -> RetrospectiveSourceReadiness:
    dataset = dataset_map.get(source)
    if dataset is None:
        return RetrospectiveSourceReadiness(
            source=source,
            role=role,
            status="missing",
            relation="unavailable",
            row_count=None,
            first_date=None,
            last_date=None,
            blocking=blocking,
            reason="source contract is absent from materialization audit",
            materialization_hint="",
        )

    coverage = materialized_dataset_coverage(dataset, start=start, end=end)
    relation = str(coverage.get("relation") or "unknown")
    fully_covers = coverage.get("fully_covers_requested_window") is True
    status = "ready" if dataset.status == "ready" and fully_covers else "partial"
    if relation == "unavailable" or dataset.status in {"missing", "error", "degraded"}:
        status = "missing" if dataset.status == "missing" else "degraded"
    elif relation == "undated" and dataset.status == "ready":
        status = "ready"

    return RetrospectiveSourceReadiness(
        source=source,
        role=role,
        status=status,
        relation=relation,
        row_count=dataset.row_count,
        first_date=dataset.first_date,
        last_date=dataset.last_date,
        blocking=blocking,
        reason=dataset.reason,
        materialization_hint=dataset.materialization_hint,
    )


def _caveats(
    *,
    rows: Iterable[RetrospectiveSourceReadiness],
    require_polylogue: bool,
) -> tuple[str, ...]:
    caveats: list[str] = []
    for row in rows:
        if row.status != "ready":
            severity = "blocks behavioral explanation" if row.blocking else "limits context"
            caveats.append(
                f"{row.source}: {row.status}/{row.relation} ({severity}); materialize: {row.materialization_hint}"
            )
    if not require_polylogue:
        caveats.append("Polylogue chat semantics are caveated by default and do not block non-AI behavioral retrospectives")
    return tuple(caveats)


__all__ = [
    "CORE_BEHAVIORAL_SOURCES",
    "CONTEXT_SOURCES",
    "OperatorRetrospectiveReadiness",
    "RetrospectiveSourceReadiness",
    "operator_retrospective_readiness",
]
