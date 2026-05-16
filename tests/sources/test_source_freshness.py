from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from lynchpin.sources import freshness


def test_source_freshness_flags_stale_observed_source(monkeypatch) -> None:
    monkeypatch.setattr(
        freshness,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"spotify": True}),
    )
    monkeypatch.setattr(
        freshness,
        "_source_observed_date",
        lambda *_args, **_kwargs: (date(2025, 12, 18), "substrate", None),
    )

    rows = freshness.source_freshness(today=date(2026, 5, 16))

    assert rows[0] == freshness.SourceFreshness(
        source="spotify",
        available=True,
        last_observed=date(2025, 12, 18),
        basis="substrate",
        stale=True,
        recommendation="Request new Spotify GDPR export",
        path=None,
    )


def test_source_freshness_accepts_substrate_dates_without_importing_substrate(monkeypatch) -> None:
    monkeypatch.setattr(
        freshness,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"spotify": True}),
    )

    rows = freshness.source_freshness(
        today=date(2026, 5, 16),
        substrate_dates={"spotify": date(2025, 12, 18)},
    )

    assert rows[0].last_observed == date(2025, 12, 18)
    assert rows[0].basis == "substrate"


def test_source_freshness_does_not_invent_unavailable_dates(monkeypatch) -> None:
    path = Path("/missing/calendar.jsonl")
    monkeypatch.setattr(
        freshness,
        "get_config",
        lambda: SimpleNamespace(available_sources=lambda: {"calendar": False}),
    )
    monkeypatch.setattr(
        freshness,
        "_source_observed_date",
        lambda *_args, **_kwargs: (None, None, path),
    )

    rows = freshness.source_freshness(today=date(2026, 5, 16))

    assert rows[0].available is False
    assert rows[0].last_observed is None
    assert rows[0].basis is None
    assert rows[0].stale is False
    assert rows[0].recommendation == "Produce /realm/data/exports/google/processed/calendar.jsonl"
    assert rows[0].path == str(path)


def test_source_freshness_caches_default_today_path(monkeypatch) -> None:
    freshness._cached_source_freshness.cache_clear()
    calls = 0

    def compute(reference: date, _substrate_dates):
        nonlocal calls
        calls += 1
        return (
            freshness.SourceFreshness(
                source="spotify",
                available=True,
                last_observed=reference,
                basis="test",
                stale=False,
                recommendation=None,
            ),
        )

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 5, 16)

    monkeypatch.setattr(freshness, "_compute_source_freshness", compute)
    monkeypatch.setattr(freshness, "date", FixedDate)
    monkeypatch.setattr(
        freshness,
        "get_config",
        lambda: SimpleNamespace(
            available_sources=lambda: {"spotify": True},
            local_root=Path("/tmp/lynchpin-local"),
            captures_root=Path("/tmp/lynchpin-captures"),
            exports_root=Path("/tmp/lynchpin-exports"),
        ),
    )

    assert freshness.source_freshness() == freshness.source_freshness()
    assert calls == 1
    freshness._cached_source_freshness.cache_clear()


def test_source_freshness_cache_key_tracks_config_roots(monkeypatch) -> None:
    freshness._cached_source_freshness.cache_clear()
    calls = 0
    local_root = Path("/tmp/lynchpin-local-a")

    def compute(reference: date, _substrate_dates):
        nonlocal calls
        calls += 1
        return (
            freshness.SourceFreshness(
                source="spotify",
                available=True,
                last_observed=reference,
                basis=str(local_root),
                stale=False,
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

    monkeypatch.setattr(freshness, "_compute_source_freshness", compute)
    monkeypatch.setattr(freshness, "date", FixedDate)
    monkeypatch.setattr(freshness, "get_config", config)

    first = freshness.source_freshness()
    local_root = Path("/tmp/lynchpin-local-b")
    second = freshness.source_freshness()

    assert first[0].basis == "/tmp/lynchpin-local-a"
    assert second[0].basis == "/tmp/lynchpin-local-b"
    assert calls == 2
    freshness._cached_source_freshness.cache_clear()
