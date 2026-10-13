"""Canonical source coverage audit for evidence-producing inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..materialization import MaterializedDataset, materialized_dataset_coverage

CoverageStatus = str


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    status: CoverageStatus
    reason: str
    requested_start: date
    requested_end: date
    first_date: date | None = None
    last_date: date | None = None
    row_count: int | None = None
    path: str | None = None
    basis: str = "source"
    repair_hint: str | None = None
    fully_covers_requested_window: bool | None = None
    covered_days: int | None = None
    requested_days: int | None = None

    @property
    def covers_requested_window(self) -> bool:
        return self.fully_covers_requested_window is True

    @property
    def intersects_requested_window(self) -> bool:
        return bool(
            self.first_date is not None
            and self.last_date is not None
            and self.first_date < self.requested_end
            and self.last_date >= self.requested_start
        )


@dataclass(frozen=True)
class CoverageReport:
    start: date
    end: date
    generated_at: datetime
    sources: tuple[SourceCoverage, ...]
    service_evidence: dict[str, Any] | None = None

    def by_source(self) -> dict[str, SourceCoverage]:
        return {source.source: source for source in self.sources}


def coverage_report(
    *,
    start: date,
    end: date,
    repair_materializations: bool = True,
) -> CoverageReport:
    cfg = get_config()
    from ..materialization import audit_materialization
    from ..core.source_contracts import source_contract

    # A small set of per-source overrides: display name (if different from
    # the contract name) and an actionable repair hint when the materializer
    # can't fix it locally. Everything else is derived from the materialized
    # dataset itself — path is the first materialized output, basis falls
    # back to "canonical-ndjson", repair hint defaults to the contract's
    # materialization_hint.
    _OVERRIDES: dict[str, dict[str, str]] = {
        "atuin": {"display": "terminal"},
        "health": {
            "repair_hint": "Run python -m lynchpin.cli.process_health if raw export is newer; otherwise replace Samsung Health export",
        },
        "sleep": {"repair_hint": "Replace Samsung Health/Sleep-as-Android export"},
        "spotify": {"repair_hint": "Request a fresh Spotify GDPR export"},
        "reddit": {"repair_hint": "Request a fresh Reddit GDPR export"},
        "facebook_messenger": {"display": "messenger", "repair_hint": "Request a fresh Facebook Messenger export"},
        "raindrop": {"repair_hint": "Request a fresh Raindrop export"},
        "substance": {
            "repair_hint": "Extend /realm/health/processed/substance_log_unified.csv with current rows",
        },
        "webhistory": {
            "repair_hint": "Add a newer browser capture/Takeout archive, then run python -m lynchpin.ingest.webhistory",
        },
    }

    audited = list(audit_materialization(cfg=cfg))
    if repair_materializations and _ensure_coverage_materializations(audited, start=start, end=end, cfg=cfg):
        audited = list(audit_materialization(cfg=cfg))

    rows = []
    for dataset in audited:
        try:
            contract = source_contract(dataset.name)
        except KeyError:
            contract = None
        # Non-temporal sources (title classifications keyed by title_hash,
        # the substrate-promotion stage) and derived metadata rollups don't
        # have an intrinsic coverage interval. Their usefulness for a query
        # window is determined by the temporal sources they classify or
        # aggregate, not by their own row dates. Skip them in the per-query
        # coverage report.
        if contract is not None and contract.collection_model in {"metadata", "stage"}:
            continue
        override = _OVERRIDES.get(dataset.name, {})
        display = override.get("display", dataset.name)
        path = dataset.materialized_paths[0] if dataset.materialized_paths else None
        default_hint = f"Materialize: {contract.materialization_hint}" if contract else None
        repair_hint = override.get("repair_hint", default_hint)
        rows.append(
            _from_materialized_dataset(
                display,
                dataset,
                start=start,
                end=end,
                path=path,
                basis="canonical-ndjson",
                repair_hint=repair_hint,
            )
        )
    from .capture_service_evidence import capture_service_evidence
    return CoverageReport(
        start=start,
        end=end,
        generated_at=datetime.now(timezone.utc),
        sources=tuple(sorted(rows, key=lambda item: item.source)),
        service_evidence=capture_service_evidence(
            start=datetime.combine(start, datetime.min.time(), timezone.utc),
            end=datetime.combine(end, datetime.min.time(), timezone.utc), cfg=cfg,
        ) if hasattr(cfg, "sinnix_runtime_inventory_json") else None,
    )


def _ensure_coverage_materializations(
    datasets: list[object],
    *,
    start: date,
    end: date,
    cfg: object,
) -> bool:
    from ..core.source_contracts import source_contract
    from ..materialization import ensure_materialized

    changed = False
    for dataset in datasets:
        name = getattr(dataset, "name", None)
        if not isinstance(name, str):
            continue
        try:
            contract = source_contract(name)
        except KeyError:
            continue
        if contract.collection_model in {"metadata", "stage"}:
            continue
        if contract.materialization_mode != "transparent":
            continue
        result = ensure_materialized(name, window=(start, end), budget="inline", cfg=cfg)
        changed = changed or result.changed
    return changed


def render_coverage_report(report: CoverageReport) -> str:
    lines = [
        "| Source | Status | Rows | Coverage | Basis | Repair |",
        "|---|---:|---:|---|---|---|",
    ]
    for row in report.sources:
        coverage = _coverage_text(row)
        repair = (row.repair_hint or "").replace("|", "\\|")
        reason = row.reason.replace("|", "\\|")
        basis = row.basis.replace("|", "\\|")
        if reason:
            basis = f"{basis}<br>{reason}"
        lines.append(
            f"| {row.source} | {row.status} | {row.row_count if row.row_count is not None else ''} | {coverage} | {basis} | {repair} |"
        )
    if report.service_evidence is not None:
        from .capture_service_evidence import render_capture_service_evidence
        lines += ["",render_capture_service_evidence(report.service_evidence)]
    return "\n".join(lines)


def _row(
    source: str,
    start: date,
    end: date,
    *,
    status: CoverageStatus,
    reason: str,
    first: date | None = None,
    last: date | None = None,
    count: int | None = None,
    path: Path | str | None = None,
    basis: str = "source",
    repair_hint: str | None = None,
    fully_covers: bool | None = None,
    covered_days: int | None = None,
    requested_days: int | None = None,
) -> SourceCoverage:
    return SourceCoverage(
        source=source,
        status=status,
        reason=reason,
        requested_start=start,
        requested_end=end,
        first_date=first,
        last_date=last,
        row_count=count,
        path=str(path) if path is not None else None,
        basis=basis,
        repair_hint=repair_hint,
        fully_covers_requested_window=fully_covers,
        covered_days=covered_days,
        requested_days=requested_days,
    )


def _from_materialized_dataset(
    source: str,
    dataset: MaterializedDataset,
    *,
    start: date,
    end: date,
    path: Path | str | None,
    basis: str,
    repair_hint: str | None = None,
) -> SourceCoverage:
    coverage = materialized_dataset_coverage(dataset, start=start, end=end)
    status_value = dataset.status
    if status_value != "ready":
        status: CoverageStatus = "missing" if status_value == "missing" else "partial"
        reason = f"materialized product is {status_value}: {getattr(dataset, 'reason', '')}"
        return _row(
            source,
            start,
            end,
            status=status,
            reason=reason,
            first=dataset.first_date,
            last=dataset.last_date,
            count=dataset.row_count,
            path=path,
            basis=basis,
            repair_hint=repair_hint,
            fully_covers=coverage["fully_covers_requested_window"],
            covered_days=coverage["covered_days"],
            requested_days=coverage["requested_days"],
        )
    first = getattr(dataset, "first_date", None)
    last = getattr(dataset, "last_date", None)
    relation = coverage["relation"]
    status = {
        "covers_window": "available",
        "no_overlap": "out_of_range",
        "undated": "missing",
    }.get(relation, "partial")
    reason = coverage["interpretation"]
    return _row(
        source,
        start,
        end,
        status=status,
        reason=reason,
        first=first,
        last=last,
        count=getattr(dataset, "row_count", None),
        path=path,
        basis=basis,
        repair_hint=repair_hint,
        fully_covers=coverage["fully_covers_requested_window"],
        covered_days=coverage["covered_days"],
        requested_days=coverage["requested_days"],
    )


def _coverage_text(row: SourceCoverage) -> str:
    if row.first_date and row.last_date:
        return f"{row.first_date.isoformat()} -> {row.last_date.isoformat()}"
    return ""


__all__ = ["CoverageReport", "SourceCoverage", "coverage_report", "render_coverage_report"]
