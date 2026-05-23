"""Canonical source coverage audit for evidence-producing inputs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
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
        return bool(
            self.first_date is not None
            and self.last_date is not None
            and self.first_date <= self.requested_start
            and self.last_date >= self.requested_end
        )

    @property
    def intersects_requested_window(self) -> bool:
        return bool(
            self.first_date is not None
            and self.last_date is not None
            and self.first_date <= self.requested_end
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


def coverage_report(*, start: date, end: date) -> CoverageReport:
    probes = (
        _activitywatch_coverage,
        _terminal_coverage,
        _webhistory_coverage,
        _sleep_coverage,
        _health_coverage,
        _spotify_coverage,
        _reddit_coverage,
        _messenger_coverage,
        _raindrop_coverage,
        _substance_coverage,
    )
    rows: list[SourceCoverage] = []
    for probe in probes:
        try:
            rows.append(probe(start, end))
        except Exception as exc:
            source = probe.__name__.removeprefix("_").removesuffix("_coverage")
            rows.append(_row(source, start, end, status="blocked", reason=f"coverage probe failed: {exc}"))
    return CoverageReport(
        start=start,
        end=end,
        generated_at=datetime.now(timezone.utc),
        sources=tuple(sorted(rows, key=lambda item: item.source)),
    )


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
    if first <= start and last >= end:
        return "available", "parsed rows cover the requested window"
    if first <= end and last >= start:
        return "partial", "parsed rows only partially cover the requested window"
    return "blocked", "parsed rows do not intersect the requested window"


def _from_dates(
    source: str,
    start: date,
    end: date,
    dates: Iterable[date | None],
    *,
    path: Path | str | None,
    basis: str = "source",
    repair_hint: str | None = None,
) -> SourceCoverage:
    first: date | None = None
    last: date | None = None
    count = 0
    for day in dates:
        if day is None:
            continue
        count += 1
        first = day if first is None or day < first else first
        last = day if last is None or day > last else last
    status, reason = _coverage_status(first, last, start, end)
    return _row(source, start, end, status=status, reason=reason, first=first, last=last, count=count, path=path, basis=basis, repair_hint=repair_hint)


def _activitywatch_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.activitywatch_raw import event_bounds

    cfg = get_config()
    first, last, count = event_bounds("aw-watcher-window_")
    status, reason = _coverage_status(first, last, start, end)
    hint = None if status == "available" else "Run python -m lynchpin.cli.process_activitywatch_archives, then re-run coverage audit"
    return _row("activitywatch", start, end, status=status, reason=reason, first=first, last=last, count=count, path=cfg.activitywatch_db, basis="sqlite", repair_hint=hint)


def _terminal_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.terminal import commands

    s_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    e_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)
    return _from_dates("terminal", start, end, (cmd.timestamp.date() for cmd in commands(start=s_dt, end=e_dt)), path=get_config().atuin_db, basis="source")


def _webhistory_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.web import _iter_all_visits

    cfg = get_config()
    return _from_dates(
        "webhistory",
        start,
        end,
        (visit.timestamp.date() for visit in _iter_all_visits()),
        path=cfg.webhistory_ndjson,
        basis="canonical-ndjson",
        repair_hint="Add a newer browser capture/Takeout archive, then run python -m lynchpin.ingest.webhistory",
    )


def _sleep_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.sleep import entries

    return _from_dates("sleep", start, end, (entry.date for entry in entries()), path=get_config().sleep_jsonl, basis="processed-jsonl", repair_hint="Refresh Samsung Health/Sleep-as-Android export")


def _health_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.health_daily import daily_health_summary

    cfg = get_config()
    return _from_dates("health", start, end, (row.date for row in daily_health_summary()), path=cfg.samsung_gdpr_cloud_dir, basis="source", repair_hint="Run python -m lynchpin.cli.process_health if raw export is newer; otherwise refresh Samsung Health export")


def _spotify_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.spotify import iter_streams

    return _from_dates("spotify", start, end, (stream.end_time.date() if stream.end_time else None for stream in iter_streams()), path=get_config().spotify_root, basis="source", repair_hint="Request a fresh Spotify GDPR export")


def _reddit_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.reddit import iter_comments, iter_posts

    dates = (
        item.created.date()
        for iterator in (iter_comments(), iter_posts())
        for item in iterator
        if item.created is not None
    )
    return _from_dates("reddit", start, end, dates, path=get_config().reddit_export_dir, basis="source", repair_hint="Request a fresh Reddit GDPR export")


def _messenger_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.exports import iter_fbmessenger_messages

    cfg = get_config()
    return _from_dates("messenger", start, end, (msg.timestamp.date() for msg in iter_fbmessenger_messages() if msg.timestamp), path=cfg.fbmessenger_gdpr_root, basis="source", repair_hint="Request a fresh Facebook Messenger export")


def _raindrop_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.exports import iter_raindrop_bookmarks

    return _from_dates("raindrop", start, end, (bm.created.date() for bm in iter_raindrop_bookmarks() if bm.created), path=get_config().raindrop_csv, basis="source", repair_hint="Request a fresh Raindrop export")


def _substance_coverage(start: date, end: date) -> SourceCoverage:
    from ..sources.substance import entries

    cfg = get_config()
    return _from_dates(
        "substance",
        start,
        end,
        (entry.date for entry in entries()),
        path=cfg.exports_root / "health/processed/substance_log_unified.csv",
        basis="processed-csv",
        repair_hint="Extend /realm/data/exports/health/processed/substance_log_unified.csv with current rows",
    )


def _coverage_text(row: SourceCoverage) -> str:
    if row.first_date and row.last_date:
        return f"{row.first_date.isoformat()} -> {row.last_date.isoformat()}"
    return ""


__all__ = ["CoverageReport", "SourceCoverage", "coverage_report", "render_coverage_report"]
