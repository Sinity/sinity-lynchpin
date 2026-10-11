from datetime import date, timezone

from lynchpin.analysis.active.substrate_promote_work import _work_window_bounds


def test_work_window_includes_current_day_live_tail() -> None:
    start, end = _work_window_bounds(
        date(2026, 4, 1),
        date(2026, 5, 31),
        today=date(2026, 5, 31),
    )

    assert start.isoformat() == "2026-04-01T00:00:00+00:00"
    assert end.isoformat() == "2026-06-01T00:00:00+00:00"
    assert start.tzinfo is timezone.utc
    assert end.tzinfo is timezone.utc


def test_work_window_preserves_future_exclusive_end() -> None:
    _, end = _work_window_bounds(
        date(2026, 4, 1),
        date(2026, 6, 5),
        today=date(2026, 5, 31),
    )

    assert end.isoformat() == "2026-06-05T00:00:00+00:00"


def test_work_window_preserves_historical_exclusive_end() -> None:
    _, end = _work_window_bounds(date(2026, 1, 15), date(2026, 1, 16), today=date(2026, 5, 31))
    assert end.isoformat() == "2026-01-16T00:00:00+00:00"


def test_missing_xtask_is_not_measured_empty(tmp_path, monkeypatch):
    import duckdb
    from types import SimpleNamespace
    from lynchpin.analysis.active.substrate_promote_work import promote_work_sources
    from lynchpin.analysis.active.substrate_promote_status import SourceSelection
    from lynchpin.substrate.connection import apply_schema
    empty = SimpleNamespace(observations=(), rejected=())
    monkeypatch.setattr("lynchpin.sources.xtask_history.xtask_history_path", lambda: tmp_path / "missing.db")
    monkeypatch.setattr("lynchpin.sources.agentctl.read_observation_snapshot", lambda: empty)
    monkeypatch.setattr("lynchpin.sources.polylogue_verification.read_verification_snapshot", lambda: empty)
    with duckdb.connect() as conn:
        apply_schema(conn)
        promote_work_sources(conn, refresh_id="fixture", window_start=date(2026, 1, 1), window_end=date(2026, 1, 2), counts={}, selection=SourceSelection.from_collection({"work_observations"}))
        status, reason, count = conn.execute("SELECT status,reason,row_count FROM substrate_source_status WHERE refresh_id='fixture'").fetchone()
        assert status == "degraded" and "xtask" in reason and "unavailable" in reason
        assert count == 0  # Physical rows are zero; the source was not measured.

        # A configured, observed empty ledger is a distinct positive control.
        (tmp_path / "missing.db").touch()
        for reader in ("iter_all_invocations", "iter_all_stage_timings", "iter_all_test_results"):
            monkeypatch.setattr("lynchpin.sources.xtask_history." + reader, lambda **kwargs: iter(()))
        promote_work_sources(conn, refresh_id="empty-fixture", window_start=date(2026, 1, 1), window_end=date(2026, 1, 2), counts={}, selection=SourceSelection.from_collection({"work_observations"}))
        assert conn.execute("SELECT status,row_count FROM substrate_source_status WHERE refresh_id='empty-fixture'").fetchone() == ("empty", 0)
