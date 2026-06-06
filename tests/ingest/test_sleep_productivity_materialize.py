from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from lynchpin.ingest.sleep_productivity_materialize import (
    SLEEP_PRODUCTIVITY_SCHEMA_VERSION,
    materialize_sleep_productivity,
)
from lynchpin.sources.sleep_productivity import iter_sleep_productivity


def test_materialize_sleep_productivity_merges_window_and_tracks_coverage(monkeypatch, tmp_path) -> None:
    output = tmp_path / "productivity.ndjson"
    output.write_text(
        json.dumps(
            {
                "sleep_date": "2026-05-01",
                "sleep_hours": 7.0,
                "sleep_score": 80.0,
                "sleep_quality": "good",
                "workday_active_hours": 5.0,
                "workday_deep_work_min": 60.0,
                "productivity_vs_baseline": 1.0,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps({"covered_dates": ["2026-05-01"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lynchpin.ingest.sleep_productivity_materialize.sleep_productivity",
        lambda *, start, end: [
            SimpleNamespace(
                sleep_date=date(2026, 5, 2),
                sleep_hours=6.5,
                sleep_score=70.0,
                sleep_quality="fair",
                workday_active_hours=4.0,
                workday_deep_work_min=45.0,
                productivity_vs_baseline=0.8,
            )
        ],
    )
    monkeypatch.setattr(
        "lynchpin.ingest.sleep_productivity_materialize._sleep_productivity_input_files",
        lambda start, end: (),
    )

    manifest = materialize_sleep_productivity(
        output=output,
        start=date(2026, 5, 2),
        end=date(2026, 5, 4),
    )
    rows = list(iter_sleep_productivity(output))

    assert [(row.sleep_date, row.sleep_hours) for row in rows] == [
        (date(2026, 5, 1), 7.0),
        (date(2026, 5, 2), 6.5),
    ]
    assert manifest["covered_dates"] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert manifest["schema_version"] == SLEEP_PRODUCTIVITY_SCHEMA_VERSION
    assert manifest["window_semantics"] == "start inclusive, end exclusive"


def test_iter_sleep_productivity_converges_default_materialization(monkeypatch, tmp_path) -> None:
    from lynchpin.sources import sleep_productivity

    product = tmp_path / "productivity.ndjson"
    product.write_text(
        json.dumps(
            {
                "sleep_date": "2026-05-24",
                "sleep_hours": 7.0,
                "sleep_score": 80.0,
                "sleep_quality": "good",
                "workday_active_hours": 5.0,
                "workday_deep_work_min": 60.0,
                "productivity_vs_baseline": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(sleep_productivity, "sleep_productivity_path", lambda root=None: product)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    rows = list(sleep_productivity.iter_sleep_productivity(start=date(2026, 5, 24), end=date(2026, 5, 25)))

    assert [(row.sleep_date, row.sleep_hours) for row in rows] == [(date(2026, 5, 24), 7.0)]
    assert calls == [("sleep_productivity", (date(2026, 5, 24), date(2026, 5, 25)))]

    calls.clear()
    assert [row.sleep_hours for row in sleep_productivity.iter_sleep_productivity(product)] == [7.0]
    assert calls == []
