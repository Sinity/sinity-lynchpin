"""Canonical source coverage audit for evidence-producing inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..core.config import get_config

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

    @property
    def covers_requested_window(self) -> bool:
        requested_last = self.requested_end - timedelta(days=1)
        return bool(
            self.first_date is not None
            and self.last_date is not None
            and self.first_date <= self.requested_start
            and self.last_date >= requested_last
        )

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
            "repair_hint": "Extend /realm/data/exports/health/processed/substance_log_unified.csv with current rows",
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
    return CoverageReport(
        start=start,
        end=end,
        generated_at=datetime.now(timezone.utc),
        sources=tuple(sorted(rows, key=lambda item: item.source)),
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
        if contract.materialization_mode != "local":
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
            f"| {row.source} | {row.status} | {row.row_count or ''} | {coverage} | {basis} | {repair} |"
        )
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
    )


def _coverage_status(first: date | None, last: date | None, start: date, end: date) -> tuple[CoverageStatus, str]:
    if first is None or last is None:
        return "missing", "no parsed rows"
    requested_last = end - timedelta(days=1)
    if first <= start and last >= requested_last:
        return "available", "parsed rows cover the requested window"
    if first < end and last >= start:
        return "partial", "parsed rows only partially cover the requested window"
    return (
        "out_of_range",
        "parsed rows do not intersect the requested window",
    )


def _from_materialized_dataset(
    source: str,
    dataset: object,
    *,
    start: date,
    end: date,
    path: Path | str | None,
    basis: str,
    repair_hint: str | None = None,
) -> SourceCoverage:
    status_value = getattr(dataset, "status", None)
    if status_value != "ready":
        status: CoverageStatus = "missing" if status_value == "missing" else "partial"
        reason = f"materialized product is {status_value}: {getattr(dataset, 'reason', '')}"
        return _row(
            source,
            start,
            end,
            status=status,
            reason=reason,
            count=getattr(dataset, "row_count", None),
            path=path,
            basis=basis,
            repair_hint=repair_hint,
        )
    first = getattr(dataset, "first_date", None)
    last = getattr(dataset, "last_date", None)
    status, reason = _coverage_status(first, last, start, end)
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
    )


def _coverage_text(row: SourceCoverage) -> str:
    if row.first_date and row.last_date:
        return f"{row.first_date.isoformat()} -> {row.last_date.isoformat()}"
    return ""


__all__ = ["CoverageReport", "SourceCoverage", "coverage_report", "render_coverage_report"]
