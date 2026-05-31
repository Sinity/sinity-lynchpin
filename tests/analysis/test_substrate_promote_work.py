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
