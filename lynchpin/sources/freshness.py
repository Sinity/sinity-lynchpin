"""Source freshness contracts for local exports and promoted substrate tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core.config import get_config

STALE_AFTER_DAYS = 30


@dataclass(frozen=True)
class SourceFreshness:
    source: str
    available: bool
    last_observed: date | None
    basis: str | None
    stale: bool
    recommendation: str | None
    path: str | None = None


_REPAIR_HINTS = {
    "fbmessenger": "Request new Facebook GDPR export",
    "raindrop": "Request new Raindrop export",
    "reddit": "Request new Reddit GDPR export",
    "sleep": "Re-sync Samsung Health data",
    "spotify": "Request new Spotify GDPR export",
    "webhistory": "Re-enable browser history capture",
}


def source_freshness(
    today: date | None = None,
    *,
    substrate_dates: Mapping[str, date] | None = None,
) -> tuple[SourceFreshness, ...]:
    """Return one freshness contract per configured source.

    Prefer promoted substrate dates when a source has a promoted table. Fall
    back to source-owned parsers for export formats where that is cheap enough,
    then to filesystem mtime as a last observable signal. No freshness date is
    invented when the source cannot provide one.
    """
    if substrate_dates is not None:
        return _compute_source_freshness(today or date.today(), dict(substrate_dates))
    if today is None:
        return _cached_source_freshness(date.today(), _cache_key())
    return _compute_source_freshness(today, {})


@lru_cache(maxsize=8)
def _cached_source_freshness(
    reference: date,
    cache_key: tuple[tuple[tuple[str, bool], ...], str, str, str],
) -> tuple[SourceFreshness, ...]:
    _ = cache_key
    return _compute_source_freshness(reference, {})


def _cache_key() -> tuple[tuple[tuple[str, bool], ...], str, str, str]:
    cfg = get_config()
    return (
        tuple(sorted((source, bool(available)) for source, available in cfg.available_sources().items())),
        str(cfg.local_root),
        str(cfg.captures_root),
        str(cfg.exports_root),
    )


def _compute_source_freshness(
    reference: date,
    substrate_dates: Mapping[str, date],
) -> tuple[SourceFreshness, ...]:
    cfg = get_config()
    available = cfg.available_sources()
    rows: list[SourceFreshness] = []
    for source, is_available in sorted(available.items()):
        observed, basis, path = _source_observed_date(source, substrate_dates, available=is_available)
        stale = bool(observed and (reference - observed).days > STALE_AFTER_DAYS)
        hint = _REPAIR_HINTS.get(source) if stale or not is_available else None
        rows.append(SourceFreshness(
            source=source,
            available=is_available,
            last_observed=observed,
            basis=basis,
            stale=stale,
            recommendation=hint,
            path=str(path) if path is not None else None,
        ))
    return tuple(rows)


def _source_observed_date(
    source: str,
    substrate_dates: Mapping[str, date],
    *,
    available: bool,
) -> tuple[date | None, str | None, Path | None]:
    cfg = get_config()
    if source in substrate_dates:
        return substrate_dates[source], "substrate", None
    if not available:
        return None, None, _configured_path(source)
    if source == "sleep":
        from .sleep import entries

        return _max_date((entry.date for entry in entries())), "source", cfg.sleep_jsonl
    if source == "reddit":
        from .reddit import iter_comments, iter_posts

        return _max_date(
            item.created.date()
            for iterator in (iter_comments(), iter_posts())
            for item in iterator
            if item.created is not None
        ), "source", cfg.reddit_export_dir
    if source == "raindrop":
        from .exports import iter_raindrop_bookmarks

        return _max_date(
            bookmark.created.date()
            for bookmark in iter_raindrop_bookmarks()
            if bookmark.created is not None
        ), "source", cfg.raindrop_csv
    if source == "fbmessenger":
        from .exports import iter_fbmessenger_messages

        return _max_date(
            message.timestamp.date()
            for message in iter_fbmessenger_messages()
            if message.timestamp is not None
        ), "source", cfg.fbmessenger_gdpr_root
    if source == "webhistory":
        from .web import _iter_all_visits

        return _max_date((visit.timestamp.date() for visit in _iter_all_visits())), "canonical-ndjson", cfg.webhistory_ndjson
    if source == "spotify":
        from .spotify import iter_streams

        return _max_date(
            stream.end_time.date()
            for stream in iter_streams()
            if stream.end_time is not None
        ), "source", cfg.spotify_root
    source_path = _configured_path(source)
    return _mtime_date(source_path), "filesystem" if source_path else None, source_path


def _configured_path(source: str) -> Path | None:
    cfg = get_config()
    mapping: dict[str, Path | None] = {
        "activitywatch": cfg.activitywatch_db,
        "atuin": cfg.atuin_db,
        "git_baseline": cfg.baseline_dir / "git_numstat.jsonl",
        "goodreads": cfg.goodreads_library,
        "machine": cfg.machine_telemetry_db,
        "polylogue": cfg.polylogue_db,
        "raw_log": cfg.raw_log_file,
        "spotify": cfg.spotify_root,
    }
    return mapping.get(source)


def _max_date(values: Any) -> date | None:
    latest: date | None = None
    for value in values:
        if isinstance(value, date) and (latest is None or value > latest):
            latest = value
    return latest


def _mtime_date(path: Path | None) -> date | None:
    if path is None:
        return None
    try:
        if path.exists():
            return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None
    return None


__all__ = ["SourceFreshness", "source_freshness"]
