from datetime import datetime, timezone

from lynchpin.analysis.machine.command_performance import (
    _best_state,
    _state_rows,
)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 1, hour, minute, tzinfo=timezone.utc)


def test_state_rows_preparse_and_sort_intervals():
    states = _state_rows(
        {
            "windows": [
                {"started_at": _dt(11).isoformat(), "ended_at": _dt(12).isoformat(), "work_state": "late"},
                {"started_at": _dt(9).isoformat(), "ended_at": _dt(10).isoformat(), "work_state": "early"},
                {"started_at": "not-a-date", "ended_at": _dt(13).isoformat(), "work_state": "bad"},
            ]
        }
    )

    assert [state.row["work_state"] for state in states] == ["early", "late"]


def test_best_state_uses_largest_overlap_from_preparsed_windows():
    states = _state_rows(
        {
            "windows": [
                {"started_at": _dt(9).isoformat(), "ended_at": _dt(10).isoformat(), "work_state": "short"},
                {"started_at": _dt(9, 30).isoformat(), "ended_at": _dt(11).isoformat(), "work_state": "long"},
            ]
        }
    )

    state, overlap = _best_state(_dt(9, 45), _dt(10, 15), states)

    assert state is not None
    assert state["work_state"] == "long"
    assert overlap == 1800
