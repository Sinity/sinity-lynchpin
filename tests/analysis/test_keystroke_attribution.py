from __future__ import annotations

from datetime import date

from lynchpin.analysis import keystroke_attribution as ka


def test_keystrokes_by_rejects_unknown_dimension():
    import pytest
    with pytest.raises(ValueError):
        ka.keystrokes_by(dimension="bogus", start=date(2026, 1, 1), end=date(2026, 1, 2))


def test_keystrokes_by_accepts_known_dimensions():
    # No data → empty rollup, but no exception for any registered dimension.
    for dim in ("app", "project", "mode", "activity", "content_type",
                "attention_level", "topic_category", "platform"):
        r = ka.keystrokes_by(dimension=dim, start=date(1990, 1, 1), end=date(1990, 1, 2))
        assert r.dimension == dim
        assert r.total_keystrokes == 0
        assert r.buckets == {}


def test_keystrokes_daily_emits_every_date_in_range(monkeypatch):
    """Days entirely absent from AW capture should still appear with
    is_offline=True so consumers can distinguish 'no data' from 'low activity'."""
    # Mock the two underlying calls; one returns spans for a single date,
    # the other returns active-seconds for a different date.
    def fake_iter(start, end):
        # yield one span on 2026-02-10 with 100 keystrokes
        yield (date(2026, 2, 10), "kitty", "", "lyn", "code", 100)

    def fake_active(*, start, end):
        return {date(2026, 2, 10): 36000, date(2026, 2, 11): 18000}  # 10h, 5h

    monkeypatch.setattr(ka, "_iter_keyed_spans", fake_iter)
    import lynchpin.sources.activitywatch as aw_mod
    monkeypatch.setattr(aw_mod, "active_seconds_by_date", fake_active)

    rows = ka.keystrokes_daily(start=date(2026, 2, 10), end=date(2026, 2, 14))
    assert [r["date"] for r in rows] == [
        "2026-02-10", "2026-02-11", "2026-02-12", "2026-02-13",
    ]
    by_d = {r["date"]: r for r in rows}
    assert by_d["2026-02-10"]["keystrokes"] == 100
    assert by_d["2026-02-10"]["is_offline"] is False
    assert by_d["2026-02-11"]["keystrokes"] == 0
    assert by_d["2026-02-11"]["is_offline"] is False  # 5h is above the 2h threshold
    # 2026-02-12 and 2026-02-13 are entirely absent from AW; should be
    # offline=True so consumers don't mistake them for active-but-silent days.
    assert by_d["2026-02-12"]["keystrokes"] == 0
    assert by_d["2026-02-12"]["active_hours"] == 0.0
    assert by_d["2026-02-12"]["is_offline"] is True
    assert by_d["2026-02-13"]["is_offline"] is True


def test_keystrokes_daily_offline_threshold_is_configurable(monkeypatch):
    def fake_iter(start, end):
        return iter(())

    def fake_active(*, start, end):
        return {date(2026, 2, 10): 3600}  # 1h

    monkeypatch.setattr(ka, "_iter_keyed_spans", fake_iter)
    import lynchpin.sources.activitywatch as aw_mod
    monkeypatch.setattr(aw_mod, "active_seconds_by_date", fake_active)

    # Default 2h threshold: 1h day is offline.
    rows_default = ka.keystrokes_daily(start=date(2026, 2, 10), end=date(2026, 2, 11))
    assert rows_default[0]["is_offline"] is True

    # 0.5h threshold: 1h is now above, not offline.
    rows_loose = ka.keystrokes_daily(
        start=date(2026, 2, 10), end=date(2026, 2, 11), offline_hours_threshold=0.5,
    )
    assert rows_loose[0]["is_offline"] is False
