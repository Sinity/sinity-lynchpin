"""Source observation contracts for local exports and promoted substrate tables.

This module reports what date bounds can be observed. It intentionally does
not decide that an old export is stale: for event/export sources, no recent
rows can mean zero activity, not a broken dataset.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from ..core.config import get_config


@dataclass(frozen=True)
class SourceObservation:
    source: str
    available: bool
    last_observed: date | None
    basis: str | None
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


def source_observations(
    today: date | None = None,
    *,
    substrate_dates: Mapping[str, date] | None = None,
) -> tuple[SourceObservation, ...]:
    """Return one observation contract per configured source.

    Prefer promoted substrate dates, then materialized product bounds, then
    filesystem mtime as the last observable signal. No date is invented when
    the source cannot provide one, and no date-age threshold is applied.
    """
    if substrate_dates is not None:
        return _compute_source_observations(today or date.today(), dict(substrate_dates))
    if today is None:
        return _cached_source_observations(date.today(), _cache_key())
    return _compute_source_observations(today, {})


@lru_cache(maxsize=8)
def _cached_source_observations(
    reference: date,
    cache_key: tuple[tuple[tuple[str, bool], ...], str, str, str],
) -> tuple[SourceObservation, ...]:
    _ = cache_key
    return _compute_source_observations(reference, {})


def _cache_key() -> tuple[tuple[tuple[str, bool], ...], str, str, str]:
    cfg = get_config()
    return (
        tuple(sorted((source, bool(available)) for source, available in cfg.available_sources().items())),
        str(cfg.local_root),
        str(cfg.captures_root),
        str(cfg.exports_root),
    )


def _compute_source_observations(
    reference: date,
    substrate_dates: Mapping[str, date],
) -> tuple[SourceObservation, ...]:
    cfg = get_config()
    available = cfg.available_sources()
    materialized_dates = _materialized_last_dates()
    rows: list[SourceObservation] = []
    for source, is_available in sorted(available.items()):
        observed, basis, path = _source_observed_date(
            source,
            substrate_dates,
            materialized_dates,
            available=is_available,
        )
        _ = reference
        hint = _REPAIR_HINTS.get(source) if not is_available else None
        rows.append(SourceObservation(
            source=source,
            available=is_available,
            last_observed=observed,
            basis=basis,
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
    """Map source-observation keys to (last_date, manifest_path) tuples.

    Drives the ``basis="materialized"`` tier of ``_source_observed_date``.
    The source-observation vocabulary differs slightly from the dataset
    contract vocabulary (``fbmessenger`` here vs. ``facebook_messenger`` in
    source_contracts); explicit aliases below cover the renames. Every
    dataset with a materialized ``last_date`` in the audit gets exposed
    so the filesystem-mtime fallback only fires for sources that have no
    manifest at all.
    """
    from lynchpin.materialization import audit_materialization

    # Source-observation key → dataset-contract name (only where they differ).
    _aliases = {
        "fbmessenger": "facebook_messenger",
    }
    rows = {row.name: row for row in audit_materialization()}
    out: dict[str, tuple[date | None, Path | None]] = {}
    # Every dataset with materialized last_date; aliases override the key.
    for contract_name, row in rows.items():
        if row.last_date is None:
            continue
        path = row.materialized_paths[0] if row.materialized_paths else None
        out[contract_name] = (row.last_date, path)
    for source_key, contract_name in _aliases.items():
        row = rows.get(contract_name)
        if row is None or row.last_date is None:
            continue
        path = row.materialized_paths[0] if row.materialized_paths else None
        out[source_key] = (row.last_date, path)
    return out


def _configured_path(source: str) -> Path | None:
    cfg = get_config()
    mapping: dict[str, Path | None] = {
        "activitywatch": cfg.activitywatch_db,
        "arbtt": cfg.arbtt_root,
        "asciinema": cfg.asciinema_root,
        "atuin": cfg.atuin_db,
        "browser_bookmarks": cfg.browser_bookmarks_root,
        "clipboard": cfg.clipboard_live_file,
        "codex": cfg.codex_sessions_root,
        "dendron": cfg.dendron_root,
        "git_baseline": cfg.baseline_dir / "git_numstat.jsonl",
        "gmail_takeout": cfg.exports_root / "google/raw/takeout",
        "goodreads": cfg.goodreads_library,
        "irc": cfg.irc_root,
        "irc_raw": cfg.irc_root / "_raw",
        "keylog": cfg.keylog_root,
        "raindrop_live": cfg.repo_root / ".lynchpin/raindrop_last_cursor.json",
        "machine": cfg.machine_telemetry_db,
        "polylogue": cfg.polylogue_db,
        "raw_log": cfg.raw_log_file,
        "samsung_gdpr_cloud": cfg.samsung_gdpr_cloud_dir,
        "sinnix_runtime_inventory": cfg.sinnix_runtime_inventory_json,
        "spotify": cfg.spotify_root,
        "wykop": cfg.wykop_root,
    }
    return mapping.get(source)


def _mtime_date(path: Path | None) -> date | None:
    """Most recent observable date for a configured root.

    For files: file mtime.
    For directories: max mtime among contained regular files (one level
    of descent is the right cost/signal trade-off — a stale directory
    mtime hides activity inside; full recursion is unbounded).
    """
    if path is None:
        return None
    try:
        if not path.exists():
            return None
        if path.is_file():
            return datetime.fromtimestamp(path.stat().st_mtime).date()
        if path.is_dir():
            latest = path.stat().st_mtime
            for entry in path.iterdir():
                try:
                    entry_mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if entry_mtime > latest:
                    latest = entry_mtime
            return datetime.fromtimestamp(latest).date()
    except OSError:
        return None
    return None


__all__ = ["SourceObservation", "source_observations"]
