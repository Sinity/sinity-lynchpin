from __future__ import annotations

import csv
import json

import pytest
from datetime import datetime, timedelta, timezone

from lynchpin.core import parse
from lynchpin.sources.samsung_binning import iter_hr_bins, iter_hrv_bins, iter_stress_bins


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.mark.parametrize("bounds_kind", ["utc", "naive_local", "aware_offset"])
def test_samsung_binning_iterators_filter_by_timestamp_window(tmp_path, monkeypatch, bounds_kind) -> None:
    root = tmp_path
    stress_dir = root / "Stress Internal Data"
    stress_dir.mkdir()
    hrv_dir = root / "Health HRV"
    hrv_dir.mkdir()
    hr_dir = root / "Heart Rate"
    hr_dir.mkdir()

    outside = datetime(2026, 5, 1, 5, tzinfo=timezone.utc)
    inside = datetime(2026, 5, 1, 7, tzinfo=timezone.utc)
    after = datetime(2026, 5, 1, 9, tzinfo=timezone.utc)

    with (stress_dir / "Stress Internal Data.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("binning_data",))
        writer.writeheader()
        writer.writerow(
            {
                "binning_data": json.dumps(
                    [
                        {"start_time": _ms(outside), "end_time": _ms(outside) + 60_000, "score": 10},
                        {"start_time": _ms(inside), "end_time": _ms(inside) + 60_000, "score": 20},
                        {"start_time": _ms(after), "end_time": _ms(after) + 60_000, "score": 30},
                    ]
                )
            }
        )

    with (hrv_dir / "Health HRV.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("binning_data",))
        writer.writeheader()
        writer.writerow(
            {
                "binning_data": json.dumps(
                    [
                        {
                            "start_time": _ms(outside),
                            "end_time": _ms(outside) + 300_000,
                            "sdnn": 10,
                            "rmssd": 11,
                        },
                        {
                            "start_time": _ms(inside),
                            "end_time": _ms(inside) + 300_000,
                            "sdnn": 20,
                            "rmssd": 21,
                        },
                        {
                            "start_time": _ms(after),
                            "end_time": _ms(after) + 300_000,
                            "sdnn": 30,
                            "rmssd": 31,
                        },
                    ]
                )
                }
            )

    with (hr_dir / "Heart Rate.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("binning_data",))
        writer.writeheader()
        writer.writerow(
            {
                "binning_data": json.dumps(
                    [
                        {"start_time": _ms(outside), "heart_rate": 60},
                        {"start_time": _ms(inside), "heart_rate": 70},
                        {"start_time": _ms(after), "heart_rate": 80},
                    ]
                )
            }
        )

    start = datetime(2026, 5, 1, 6, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, 9, tzinfo=timezone.utc)

    if bounds_kind == "naive_local":
        monkeypatch.setattr(parse, "local_tz", lambda: timezone(timedelta(hours=2)))
        start = parse.as_local(start).replace(tzinfo=None)
        end = parse.as_local(end).replace(tzinfo=None)
    elif bounds_kind == "aware_offset":
        offset = timezone(timedelta(hours=5, minutes=30))
        start = start.astimezone(offset)
        end = end.astimezone(offset)

    assert [row.score for row in iter_stress_bins(root, start=start, end=end)] == [20.0]
    assert [row.sdnn for row in iter_hrv_bins(root, start=start, end=end)] == [20.0]
    assert [row.heart_rate for row in iter_hr_bins(root, start=start, end=end)] == [70.0]
