"""Tests for typing-dynamics extraction from raw keylog JSONL."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lynchpin.sources.keylog_dynamics import BURST_GAP_MS, MIN_DAY_PRESSES, daily_dynamics


def _write_day(root: Path, day: date, presses: list[datetime]) -> None:
    path = root / f"{day.isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for ts in presses:
            fh.write(json.dumps({
                "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "event": "press", "session": "s", "window": "w",
                "keycode": "KEY_X", "changed": False,
            }) + "\n")


def test_iki_and_burst_metrics(tmp_path: Path) -> None:
    day = date(2026, 6, 1)
    base = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    presses = []
    # 3 bursts of MIN_DAY_PRESSES/2 presses at a steady 150ms IKI,
    # separated by 10s pauses.
    for burst in range(3):
        start = base + timedelta(seconds=burst * 60)
        for i in range(MIN_DAY_PRESSES // 2):
            presses.append(start + timedelta(milliseconds=150 * i))
    _write_day(tmp_path, day, presses)

    rows = daily_dynamics(day, day, root=tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row.press_count == 3 * (MIN_DAY_PRESSES // 2)
    assert abs(row.median_iki_ms - 150.0) < 1.0
    assert row.burst_count == 3
    # pauses (10s >= BURST_GAP_MS) are excluded from IKI stats
    assert row.p90_iki_ms < BURST_GAP_MS


def test_sparse_day_absent_not_zero(tmp_path: Path) -> None:
    day = date(2026, 6, 1)
    base = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    _write_day(tmp_path, day, [base + timedelta(seconds=i) for i in range(10)])
    assert daily_dynamics(day, day, root=tmp_path) == []


def test_pre_boundary_presses_bucket_to_previous_logical_day(tmp_path: Path) -> None:
    # Presses at 01:30 UTC on June 2 (03:30 Warsaw) land on logical June 1,
    # even though they live in the June 2 calendar-named file.
    file_day = date(2026, 6, 2)
    base = datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)
    presses = [base + timedelta(milliseconds=150 * i) for i in range(MIN_DAY_PRESSES + 10)]
    _write_day(tmp_path, file_day, presses)

    rows = daily_dynamics(date(2026, 6, 1), date(2026, 6, 1), root=tmp_path)
    assert len(rows) == 1
    assert rows[0].date == date(2026, 6, 1)
