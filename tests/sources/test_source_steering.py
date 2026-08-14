"""Tests for the steering source."""

import json
from datetime import date

from lynchpin.sources import steering


def _write_day(root, day, rows):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def test_readiness_missing_dir(tmp_path):
    r = steering.readiness(tmp_path / "nope")
    assert r.status == "missing"


def test_readiness_counts_rows_across_tables(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"_table": "activities", "id": "a1", "name": "task one", "kind": "task", "energy_tier": "any"},
        {"_table": "reviews", "id": "r1", "ts": "2026-08-13T19:30:00+00:00", "window": "evening"},
    ])
    r = steering.readiness(tmp_path)
    assert r.status == "ok"
    assert r.row_count == 2


def test_commitments_dated_by_created_at(tmp_path):
    _write_day(tmp_path, "2026-08-14", [
        {
            "_table": "commitments", "id": "c1", "text": "deep work block",
            "created_at": "2026-08-14T07:00:00+00:00", "window_start": "morning",
            "window_end": "noon", "forecast_p": 0.7, "status": "done",
            "outcome_at": "2026-08-14T12:00:00+00:00", "outcome_note": None,
            "activity_id": "d8ac3cfd5e5c", "review_id": None,
        },
    ])
    rows = list(steering.commitments(root=tmp_path))
    assert len(rows) == 1
    row = rows[0]
    assert row.forecast_p == 0.7
    assert row.status == "done"
    assert row.date == date(2026, 8, 14)


def test_commitment_falls_back_to_export_date_without_created_at(tmp_path):
    _write_day(tmp_path, "2026-08-14", [
        {"_table": "commitments", "id": "c1", "text": "x", "created_at": None,
         "window_start": None, "window_end": None, "forecast_p": None,
         "status": "open", "outcome_at": None, "outcome_note": None,
         "activity_id": None, "review_id": None},
    ])
    rows = list(steering.commitments(root=tmp_path))
    assert rows[0].date == date(2026, 8, 14)


def test_activities_are_undated_catalogue_rows(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"_table": "activities", "id": "a1", "name": "physical movement / walk",
         "kind": "practice", "est_minutes": 20, "energy_tier": "low",
         "standing_notes": None, "hypothesis": None, "prereg_prediction": None,
         "metric_ref": None, "experiment_status": None},
    ])
    rows = list(steering.activities(root=tmp_path))
    assert len(rows) == 1
    assert rows[0].name == "physical movement / walk"
    assert rows[0].export_date == date(2026, 8, 13)


def test_reviews_dated_by_ts(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"_table": "reviews", "id": "r1", "ts": "2026-08-13T19:30:33+00:00",
         "window": "evening", "generated_summary": "## Evening review",
         "operator_notes": None},
    ])
    rows = list(steering.reviews(root=tmp_path))
    assert len(rows) == 1
    assert rows[0].window == "evening"
    assert rows[0].date == date(2026, 8, 13)


def test_table_filter_excludes_other_tables(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"_table": "activities", "id": "a1", "name": "x", "kind": "task", "energy_tier": "any"},
        {"_table": "reviews", "id": "r1", "ts": "2026-08-13T19:30:00+00:00", "window": "evening"},
    ])
    assert list(steering.commitments(root=tmp_path)) == []
    assert len(list(steering.activities(root=tmp_path))) == 1
    assert len(list(steering.reviews(root=tmp_path))) == 1


def test_date_window_filters_commitments_and_reviews(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"_table": "commitments", "id": "c1", "text": "x",
         "created_at": "2026-08-13T07:00:00+00:00", "window_start": None,
         "window_end": None, "forecast_p": None, "status": "open",
         "outcome_at": None, "outcome_note": None, "activity_id": None, "review_id": None},
    ])
    _write_day(tmp_path, "2026-08-14", [
        {"_table": "commitments", "id": "c2", "text": "y",
         "created_at": "2026-08-14T07:00:00+00:00", "window_start": None,
         "window_end": None, "forecast_p": None, "status": "open",
         "outcome_at": None, "outcome_note": None, "activity_id": None, "review_id": None},
    ])
    rows = list(steering.commitments(root=tmp_path, start=date(2026, 8, 14), end=date(2026, 8, 14)))
    assert len(rows) == 1
    assert rows[0].id == "c2"
