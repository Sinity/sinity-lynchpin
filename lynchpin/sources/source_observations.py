"""Source observation contracts for local exports and promoted substrate tables.

This module reports what date bounds can be observed. It intentionally does
not decide that an old export is stale: for event/export sources, no recent
rows can mean zero activity, not a broken dataset.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..core.config import get_config
from ..core.coverage import CAPTURE_SOURCES, CoverageBounds, _classify_source


@dataclass(frozen=True)
class SourceObservation:
    source: str
    available: bool
    last_observed: date | None
    basis: str | None
    recommendation: str | None
    path: str | None = None


@dataclass(frozen=True)
class _MaterializedBounds:
    first: date | None
    last: date | None
    path: Path | None


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
    return _compute_source_observations(today or date.today(), {})


def _compute_source_observations(
    reference: date,
    substrate_dates: Mapping[str, date],
    materialized_dates: Mapping[str, tuple[date | None, Path | None]] | None = None,
) -> tuple[SourceObservation, ...]:
    cfg = get_config()
    available = cfg.available_sources()
    materialized_dates = materialized_dates or _materialized_last_dates()
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
    return {
        key: (bounds.last, bounds.path)
        for key, bounds in _materialized_date_bounds().items()
        if bounds.last is not None
    }


def _materialized_date_bounds() -> dict[str, _MaterializedBounds]:
    """Map source-observation keys to first/last materialized bounds.

    ``coverage_bounds()`` needs both first and last dates. The previous
    implementation called ``audit_materialization()`` twice, which cost ~10s
    inside every operator_daily build on the live repo. Keep one audit pass and
    derive both views from it.
    """
    from lynchpin.materialization import audit_materialization

    aliases = {
        "fbmessenger": "facebook_messenger",
    }
    rows = {row.name: row for row in audit_materialization()}
    out: dict[str, _MaterializedBounds] = {}
    for contract_name, row in rows.items():
        if row.first_date is None and row.last_date is None:
            continue
        path = row.materialized_paths[0] if row.materialized_paths else None
        out[contract_name] = _MaterializedBounds(row.first_date, row.last_date, path)
    for source_key, contract_name in aliases.items():
        aliased = rows.get(contract_name)
        if aliased is None or (aliased.first_date is None and aliased.last_date is None):
            continue
        path = aliased.materialized_paths[0] if aliased.materialized_paths else None
        out[source_key] = _MaterializedBounds(aliased.first_date, aliased.last_date, path)
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
        "xtask_history": cfg.xtask_history_db,
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


def coverage_bounds(
    today: date | None = None,
    *,
    substrate_dates: Mapping[str, date] | None = None,
) -> dict[str, CoverageBounds]:
    """Return ``CoverageBounds`` keyed by source name for every configured source.

    Reuses the same observed-date discovery already performed by
    ``source_observations()`` — no additional file scanning.  Each bound is
    tagged as ``"capture"`` or ``"export"`` based on ``CAPTURE_SOURCES``.

    The ``first`` bound is populated from materialized dataset metadata when
    available; otherwise it is ``None`` (first-date was not observable from
    filesystem mtime alone).  The ``last`` bound mirrors ``last_observed``
    from ``source_observations()``.

    Parameters
    ----------
    today:
        Reference date forwarded to ``source_observations()``.
    substrate_dates:
        Optional promoted substrate dates forwarded to ``source_observations()``.

    Returns
    -------
    dict[str, CoverageBounds]
        One entry per source key returned by ``available_sources()``.
        Sources that are unavailable have ``first=None`` and ``last=None``.

    Example
    -------
    ::

        bounds = coverage_bounds()
        spotify = bounds["spotify"]
        if spotify.covers(date(2025, 12, 1)):
            ...  # safe to query
        start, end = spotify.clamp(date(2025, 1, 1), date(2026, 5, 30)) or (None, None)
        print(spotify.provenance())
        # → "spotify: covers 2020-01-01 → 2025-12-18 (export)"
    """
    materialized_bounds = _materialized_date_bounds()
    materialized_last_dates = {
        key: (bounds.last, bounds.path)
        for key, bounds in materialized_bounds.items()
        if bounds.last is not None
    }
    observations = (
        _compute_source_observations(
            today or date.today(),
            dict(substrate_dates or {}),
            materialized_last_dates,
        )
        if substrate_dates is not None or today is not None
        else _compute_source_observations(date.today(), {}, materialized_last_dates)
    )
    result: dict[str, CoverageBounds] = {}
    for obs in observations:
        first = (
            materialized_bounds[obs.source].first
            if obs.available and obs.source in materialized_bounds
            else None
        )
        result[obs.source] = CoverageBounds(
            source=obs.source,
            first=first,
            last=obs.last_observed,
            kind=_classify_source(obs.source),
        )
    return result


def _materialized_first_dates_map() -> dict[str, date]:
    """Map source key → first_date from materialized dataset manifests.

    Parallel to ``_materialized_last_dates()`` but returning the ``first_date``
    field.  Uses the same alias mapping so ``fbmessenger`` → ``facebook_messenger``.
    """
    return {
        key: bounds.first
        for key, bounds in _materialized_date_bounds().items()
        if bounds.first is not None
    }


__all__ = [
    "CoverageBounds",
    "CAPTURE_SOURCES",
    "SourceObservation",
    "coverage_bounds",
    "source_observations",
]
