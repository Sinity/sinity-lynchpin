from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from lynchpin.sources import source_observations


def test_source_observations_reports_observed_source_without_age_scoring(monkeypatch) -> None:
    monkeypatch.setattr(
        source_observations,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"spotify": True}),
    )
    monkeypatch.setattr(
        source_observations,
        "_source_observed_date",
        lambda *_args, **_kwargs: (date(2025, 12, 18), "substrate", None),
    )

    rows = source_observations.source_observations(today=date(2026, 5, 16))

    assert rows[0] == source_observations.SourceObservation(
        source="spotify",
        available=True,
        last_observed=date(2025, 12, 18),
        basis="substrate",
        recommendation=None,
        path=None,
    )


def test_source_observations_accepts_substrate_dates_without_importing_substrate(monkeypatch) -> None:
    monkeypatch.setattr(
        source_observations,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"spotify": True}),
    )

    rows = source_observations.source_observations(
        today=date(2026, 5, 16),
        substrate_dates={"spotify": date(2025, 12, 18)},
    )

    assert rows[0].last_observed == date(2025, 12, 18)
    assert rows[0].basis == "substrate"


def test_source_observations_does_not_invent_unavailable_dates(monkeypatch) -> None:
    path = Path("/missing/spotify")
    monkeypatch.setattr(
        source_observations,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"spotify": False}),
    )
    monkeypatch.setattr(
        source_observations,
        "_source_observed_date",
        lambda *_args, **_kwargs: (None, None, path),
    )

    rows = source_observations.source_observations(today=date(2026, 5, 16))

    assert rows[0].available is False
    assert rows[0].last_observed is None
    assert rows[0].basis is None
    assert rows[0].recommendation == "Request new Spotify GDPR export"
    assert rows[0].path == str(path)


def test_source_observations_caches_default_today_path(monkeypatch) -> None:
    source_observations._cached_source_observations.cache_clear()
    calls = 0

    def compute(reference: date, _substrate_dates):
        nonlocal calls
        calls += 1
        return (
            source_observations.SourceObservation(
                source="spotify",
                available=True,
                last_observed=reference,
                basis="test",
                recommendation=None,
            ),
        )

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 5, 16)

    monkeypatch.setattr(source_observations, "_compute_source_observations", compute)
    monkeypatch.setattr(source_observations, "date", FixedDate)
    monkeypatch.setattr(
        source_observations,
        "get_config",
        lambda: SimpleNamespace(
            available_sources=lambda: {"spotify": True},
            local_root=Path("/tmp/lynchpin-local"),
            captures_root=Path("/tmp/lynchpin-captures"),
            exports_root=Path("/tmp/lynchpin-exports"),
        ),
    )

    assert source_observations.source_observations() == source_observations.source_observations()
    assert calls == 1
    source_observations._cached_source_observations.cache_clear()


def test_source_observations_cache_key_tracks_config_roots(monkeypatch) -> None:
    source_observations._cached_source_observations.cache_clear()
    calls = 0
    local_root = Path("/tmp/lynchpin-local-a")

    def compute(reference: date, _substrate_dates):
        nonlocal calls
        calls += 1
        return (
            source_observations.SourceObservation(
                source="spotify",
                available=True,
                last_observed=reference,
                basis=str(local_root),
                recommendation=None,
            ),
        )

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 5, 16)

    def config():
        return SimpleNamespace(
            available_sources=lambda: {"spotify": True},
            local_root=local_root,
            captures_root=Path("/tmp/lynchpin-captures"),
            exports_root=Path("/tmp/lynchpin-exports"),
        )

    monkeypatch.setattr(source_observations, "_compute_source_observations", compute)
    monkeypatch.setattr(source_observations, "date", FixedDate)
    monkeypatch.setattr(source_observations, "get_config", config)

    first = source_observations.source_observations()
    local_root = Path("/tmp/lynchpin-local-b")
    second = source_observations.source_observations()

    assert first[0].basis == "/tmp/lynchpin-local-a"
    assert second[0].basis == "/tmp/lynchpin-local-b"
    assert calls == 2
    source_observations._cached_source_observations.cache_clear()


def test_source_observations_prefers_materialized_dates(monkeypatch, tmp_path) -> None:
    from lynchpin.materialization import MaterializedDataset

    source_path = tmp_path / "spotify.ndjson"
    rows = [
        MaterializedDataset(
            name="spotify",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(source_path,),
            raw_roots=(),
            row_count=1,
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 18),
            refresh_command="refresh",
            reason="ready",
        )
    ]
    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda: rows)
    monkeypatch.setattr(
        source_observations,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"spotify": True}),
    )

    observed, basis, path = source_observations._source_observed_date(
        "spotify",
        {},
        source_observations._materialized_last_dates(),
        available=True,
    )

    assert observed == date(2025, 12, 18)
    assert basis == "materialized"
    assert path == source_path


def test_coverage_bounds_audits_materialization_once(monkeypatch, tmp_path) -> None:
    from lynchpin.materialization import MaterializedDataset

    calls = 0
    source_path = tmp_path / "spotify.ndjson"
    rows = [
        MaterializedDataset(
            name="spotify",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(source_path,),
            raw_roots=(),
            row_count=1,
            first_date=date(2020, 1, 1),
            last_date=date(2025, 12, 18),
            refresh_command="refresh",
            reason="ready",
        )
    ]

    def audit():
        nonlocal calls
        calls += 1
        return rows

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", audit)
    monkeypatch.setattr(
        source_observations,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"spotify": True}),
    )

    bounds = source_observations.coverage_bounds(today=date(2026, 5, 16))

    assert calls == 1
    assert bounds["spotify"].first == date(2020, 1, 1)
    assert bounds["spotify"].last == date(2025, 12, 18)


def test_mtime_date_walks_directory_for_newest_entry(tmp_path) -> None:
    """Directory roots (asciinema, keylog, dendron, …) need newest
    contained file's mtime, not the directory's own — the directory mtime
    only changes on entry add/delete, not on in-place file updates.
    """
    import os

    directory = tmp_path / "captures"
    directory.mkdir()
    fresh_file = directory / "recent.jsonl"
    fresh_file.write_text("{}\n")
    # Set file mtime FIRST (newer), then directory mtime (older); create
    # order matters because write_text bumps the directory's own mtime.
    os.utime(fresh_file, (1748131200, 1748131200))  # 2025-05-25 UTC
    os.utime(directory, (1577836800, 1577836800))   # 2020-01-01 UTC

    observed = source_observations._mtime_date(directory)
    assert observed == date(2025, 5, 25), (
        "should pick the file's mtime, not the directory's older mtime"
    )


def test_every_available_source_resolves_to_at_least_one_observable() -> None:
    """source_observation_bounds reported `available=true, basis=null,
    path=null` for ~11 sources because `_configured_path` lacked entries
    for them and they had no materialized contract either. Pin that every
    available source resolves through substrate, materialization, or a
    configured path — anything else leaves a vacuous `available=true`.
    """
    from lynchpin.core.config import get_config

    cfg = get_config()
    materialized = source_observations._materialized_last_dates()
    unresolved = [
        source
        for source in cfg.available_sources()
        if source not in materialized
        and source_observations._configured_path(source) is None
    ]
    assert not unresolved, (
        f"sources without substrate/materialized/configured_path resolution: {unresolved}"
    )
