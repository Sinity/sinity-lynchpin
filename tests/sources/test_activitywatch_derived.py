from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources import activitywatch_derived
from lynchpin.sources.activitywatch_derived import (
    iter_derived_daily_activity,
    iter_derived_focus_spans,
    iter_derived_project_focus_days,
)


def test_activitywatch_derived_readers_hydrate_rows(tmp_path, monkeypatch):
    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("explicit path reads must not materialize")

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)

    focus_path = tmp_path / "focus_spans.ndjson"
    focus_path.write_text(
        json.dumps(
            {
                "start": "2026-06-06T08:00:00+00:00",
                "end": "2026-06-06T09:00:00+00:00",
                "kind": "focused",
                "app": "kitty",
                "title": "lynchpin",
                "mode": "coding",
                "project": "lynchpin",
                "keypress_count": 7,
                "keylog_state": "available",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    project_path = tmp_path / "project_focus_days.ndjson"
    project_path.write_text(
        json.dumps(
            {
                "date": "2026-06-06",
                "project": "lynchpin",
                "duration_s": 3600.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    daily_path = tmp_path / "daily_activity.ndjson"
    daily_path.write_text(
        json.dumps(
            {
                "date": "2026-06-06",
                "active_hours": 2.0,
                "deep_work_min": 45.0,
                "fragmentation_score": 0.25,
                "project_count": 1,
                "dominant_mode": "coding",
                "dominant_project": "lynchpin",
                "hourly_active": [0.0] * 8 + [60.0, 60.0] + [0.0] * 14,
                "outage_hours": 0.5,
                "presence_active_hours": 2.0,
                "presence_typing_hours": 1.0,
                "presence_data_gap_hours": 0.25,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    spans = list(
        iter_derived_focus_spans(
            start=datetime(2026, 6, 6, 7, tzinfo=timezone.utc),
            end=datetime(2026, 6, 6, 10, tzinfo=timezone.utc),
            min_duration_s=60.0,
            path=focus_path,
        )
    )
    days = list(
        iter_derived_project_focus_days(
            start=datetime(2026, 6, 6, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 6, 23, tzinfo=timezone.utc),
            path=project_path,
        )
    )
    daily = list(
        iter_derived_daily_activity(
            start=date(2026, 6, 6),
            end=date(2026, 6, 6),
            path=daily_path,
        )
    )

    assert len(spans) == 1
    assert spans[0].duration_s == 3600.0
    assert spans[0].keypress_count == 7
    assert len(days) == 1
    assert days[0].date == date(2026, 6, 6)
    assert days[0].duration_s == 3600.0
    assert len(daily) == 1
    assert daily[0].deep_work_min == 45.0
    assert daily[0].hourly_active[8] == 60.0
    assert daily[0].presence_typing_hours == 1.0


def test_activitywatch_derived_default_reader_materializes(monkeypatch, tmp_path):
    calls = []
    product = tmp_path / "activitywatch/graph/focus_spans.ndjson"
    product.parent.mkdir(parents=True)
    product.write_text(
        json.dumps(
            {
                "start": "2026-06-06T08:00:00+00:00",
                "end": "2026-06-06T09:00:00+00:00",
                "kind": "focused",
                "app": "kitty",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        activitywatch_derived,
        "get_config",
        lambda: SimpleNamespace(derived_root=tmp_path),
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    spans = list(
        iter_derived_focus_spans(
            start=datetime(2026, 6, 6, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 7, 0, tzinfo=timezone.utc),
        )
    )

    assert calls == [("activitywatch_derived", (date(2026, 6, 6), date(2026, 6, 7)))]
    assert len(spans) == 1


def test_project_focus_days_respects_half_open_datetime_end(tmp_path):
    project_path = tmp_path / "project_focus_days.ndjson"
    project_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "date": "2026-06-06",
                        "project": "lynchpin",
                        "duration_s": 3600.0,
                    }
                ),
                json.dumps(
                    {
                        "date": "2026-06-07",
                        "project": "lynchpin",
                        "duration_s": 7200.0,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    days = list(
        iter_derived_project_focus_days(
            start=datetime(2026, 6, 6, 6),
            end=datetime(2026, 6, 7, 6),
            path=project_path,
        )
    )

    assert [day.date for day in days] == [date(2026, 6, 6)]
