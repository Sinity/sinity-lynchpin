"""Source freshness contracts for local exports and promoted substrate tables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

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

    Prefer promoted substrate dates, then materialized product bounds, then
    filesystem mtime as the last observable signal. No freshness date is
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
    materialized_dates = _materialized_last_dates()
    rows: list[SourceFreshness] = []
    for source, is_available in sorted(available.items()):
        observed, basis, path = _source_observed_date(
            source,
            substrate_dates,
            materialized_dates,
            available=is_available,
        )
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
    materialized_dates: Mapping[str, tuple[date | None, Path | None]],
    *,
    available: bool,
) -> tuple[date | None, str | None, Path | None]:
    if source in substrate_dates:
        return substrate_dates[source], "substrate", None
    if not available:
        return None, None, _configured_path(source)
    materialized = materialized_dates.get(source)
    if materialized is not None:
        observed, path = materialized
        return observed, "materialized", path
    source_path = _configured_path(source)
    return _mtime_date(source_path), "filesystem" if source_path else None, source_path


def _materialized_last_dates() -> dict[str, tuple[date | None, Path | None]]:
    from lynchpin.materialization import audit_materialization

    mapping = {
        "atuin": "atuin",
        "fbmessenger": "facebook_messenger",
        "reddit": "reddit",
        "sleep": "sleep",
        "spotify": "spotify",
        "webhistory": "webhistory",
        "raindrop": "raindrop",
    }
    rows = {row.name: row for row in audit_materialization()}
    out: dict[str, tuple[date | None, Path | None]] = {}
    for source, contract in mapping.items():
        row = rows.get(contract)
        if row is None:
            continue
        path = row.materialized_paths[0] if row.materialized_paths else None
        out[source] = (row.last_date, path)
    return out


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
