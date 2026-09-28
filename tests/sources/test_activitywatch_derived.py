from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from lynchpin.sources import activitywatch_derived
from lynchpin.sources.activitywatch_derived import (
    activitywatch_derived_product_paths,
    iter_derived_daily_activity,
    iter_derived_focus_spans,
    iter_derived_project_focus_days,
)


def test_app_session_counts_observed_union_not_wall_gap():
    from lynchpin.sources.activitywatch import _app_sessions_from_spans
    from lynchpin.sources.activitywatch_models import FocusSpan

    start = datetime(2026, 6, 6, 10, tzinfo=timezone.utc)
    spans = [FocusSpan(start=start, end=start + timedelta(seconds=60), kind="focused",
                       app="editor", title="A", mode="coding", project="demo"),
             FocusSpan(start=start + timedelta(seconds=120), end=start + timedelta(seconds=180),
                       kind="focused", app="editor", title="A", mode="coding", project="demo")]
    sessions = _app_sessions_from_spans(spans, min_duration_s=60)
    assert len(sessions) == 1
    assert sessions[0].duration_s == 120
    assert (sessions[0].end - sessions[0].start).total_seconds() == 180


def test_app_session_absorbs_short_other_app_without_changing_anchor():
    from lynchpin.sources.activitywatch import _app_sessions_from_spans
    from lynchpin.sources.activitywatch_models import FocusSpan

    start = datetime(2026, 6, 6, 10, tzinfo=timezone.utc)

    def span(app, offset, seconds):
        return FocusSpan(start=start + timedelta(seconds=offset),
                         end=start + timedelta(seconds=offset + seconds),
                         kind="focused", app=app, title=app,
                         mode="coding", project="demo")

    sessions = _app_sessions_from_spans(
        [span("A", 0, 60), span("B", 70, 10), span("A", 90, 60),
         span("B", 3600, 10)], min_duration_s=1,
    )
    assert [session.app for session in sessions] == ["A", "B"]
    assert sessions[0].duration_s == 120
    assert sessions[0].interruptions == 1


def test_partition_paths_follow_relocated_derived_root(tmp_path):
    from lynchpin.ingest.activitywatch_derived_materialize import _existing_partitions

    current = tmp_path / "new" / "activitywatch" / "graph"
    partition = current / "generations" / "generation-a" / "daily_activity" / "2026-06-06.ndjson"
    partition.parent.mkdir(parents=True)
    partition.write_text('{"date":"2026-06-06"}\n', encoding="utf-8")
    old = tmp_path / "old" / "activitywatch" / "graph" / "generations" / "generation-a" / "daily_activity" / "2026-06-06.ndjson"
    manifest = {
        "product_paths": {kind: {} for kind in activitywatch_derived.PRODUCT_KINDS},
        "partition_row_counts": {kind: {} for kind in activitywatch_derived.PRODUCT_KINDS},
    }
    manifest["product_paths"]["daily_activity"]["2026-06-06"] = str(old)
    manifest["partition_row_counts"]["daily_activity"]["2026-06-06"] = 1
    (current / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert activitywatch_derived_product_paths("daily_activity", root=tmp_path / "new") == {"2026-06-06": partition}
    paths, counts = _existing_partitions(manifest, root=tmp_path / "new")
    assert paths["daily_activity"] == {"2026-06-06": partition}
    assert counts["daily_activity"] == {"2026-06-06": 1}

    partition.unlink()
    from lynchpin.core.errors import MaterializationError

    with pytest.raises(MaterializationError, match="manifest partition is unavailable"):
        _existing_partitions(manifest, root=tmp_path / "new")


def test_missing_manifest_partitions_are_named_gaps_not_empty_days(
    tmp_path, monkeypatch
):
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.sources import activitywatch_event_index
    from lynchpin.sources.activitywatch_event_index import (
        ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
        iter_indexed_activitywatch_events,
    )

    day = "2026-06-06"
    missing_derived = tmp_path / "derived" / "daily_activity.ndjson"
    derived_manifest = tmp_path / "derived" / "manifest.json"
    derived_manifest.parent.mkdir(parents=True)
    derived_manifest.write_text(
        json.dumps(
            {"product_paths": {"daily_activity": {day: str(missing_derived)}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        activitywatch_derived,
        "activitywatch_derived_manifest_path",
        lambda _root=None: derived_manifest,
    )
    with pytest.raises(SourceUnavailableError, match=f"daily_activity.*{day}"):
        list(
            iter_derived_daily_activity(
                start=date.fromisoformat(day),
                end=date.fromisoformat(day) + timedelta(days=1),
                ensure=False,
            )
        )

    empty_derived = tmp_path / "derived" / "empty.ndjson"
    empty_derived.write_text("", encoding="utf-8")
    derived_manifest.write_text(
        json.dumps({"product_paths": {"daily_activity": {day: str(empty_derived)}}}),
        encoding="utf-8",
    )
    assert list(
        iter_derived_daily_activity(
            start=date.fromisoformat(day),
            end=date.fromisoformat(day) + timedelta(days=1),
            ensure=False,
        )
    ) == []

    missing_index = tmp_path / "events" / "missing.ndjson"
    index_manifest = tmp_path / "events" / "manifest.json"
    index_manifest.parent.mkdir(parents=True)
    index_manifest.write_text(
        json.dumps(
            {
                "schema_version": ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
                "product_paths": {day: str(missing_index)},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        activitywatch_event_index,
        "activitywatch_event_index_manifest_path",
        lambda _root=None: index_manifest,
    )
    start = datetime(2026, 6, 6, 12, tzinfo=timezone.utc)
    with pytest.raises(SourceUnavailableError, match=day):
        list(
            iter_indexed_activitywatch_events(
                bucket_prefix="aw-watcher-window_",
                start=start,
                end=start + timedelta(hours=1),
                root=tmp_path,
            )
        )

    missing_index.write_text("", encoding="utf-8")
    assert list(
        iter_indexed_activitywatch_events(
            bucket_prefix="aw-watcher-window_",
            start=start,
            end=start + timedelta(hours=1),
            root=tmp_path,
        )
    ) == []


def test_default_read_reports_failed_materialization(monkeypatch):
    from lynchpin.core.errors import MaterializationError

    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda *_args, **_kwargs: SimpleNamespace(status="failed", reason="partition unavailable"),
    )
    with pytest.raises(MaterializationError, match="partition unavailable"):
        list(iter_derived_daily_activity(start=date(2026, 6, 6), end=date(2026, 6, 6)))


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


def test_partitioned_empty_product_does_not_fall_back_to_legacy_rows(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 3, "product_paths": {"daily_activity": {}}}),
        encoding="utf-8",
    )
    legacy = tmp_path / "daily_activity.ndjson"
    legacy.write_text(
        json.dumps(
            {
                "date": "2026-06-06",
                "active_hours": 99.0,
                "deep_work_min": 0.0,
                "fragmentation_score": 0.0,
                "project_count": 0,
                "hourly_active": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(activitywatch_derived, "activitywatch_derived_manifest_path", lambda root=None: manifest)
    monkeypatch.setattr(activitywatch_derived, "activitywatch_derived_path", lambda _kind: legacy)

    rows = list(
        iter_derived_daily_activity(
            start=date(2026, 6, 6),
            end=date(2026, 6, 6),
            ensure=False,
        )
    )

    assert rows == []


def test_focus_span_reader_repairs_rollback_offset_from_recorded_duration(tmp_path):
    focus_path = tmp_path / "focus_spans.ndjson"
    focus_path.write_text(
        json.dumps(
            {
                "start": "2025-10-26T02:12:25.057417+01:00",
                "end": "2025-10-26T02:18:01.055419+02:00",
                "duration_s": 335.998,
                "kind": "focused",
                "app": "google-chrome",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    spans = list(
        iter_derived_focus_spans(
            start=datetime(2025, 10, 26, 0, tzinfo=timezone.utc),
            end=datetime(2025, 10, 27, 0, tzinfo=timezone.utc),
            path=focus_path,
        )
    )

    assert len(spans) == 1
    assert spans[0].end.isoformat() == "2025-10-26T02:18:01.055417+01:00"
    assert spans[0].duration_s == 335.998


def test_focus_span_reader_rejects_invalid_row_without_duration(tmp_path):
    focus_path = tmp_path / "focus_spans.ndjson"
    focus_path.write_text(
        json.dumps(
            {
                "start": "2025-10-26T02:12:25.057417+01:00",
                "end": "2025-10-26T02:18:01.055419+02:00",
                "kind": "focused",
                "app": "google-chrome",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no usable duration"):
        list(
            iter_derived_focus_spans(
                start=datetime(2025, 10, 26, 0, tzinfo=timezone.utc),
                end=datetime(2025, 10, 27, 0, tzinfo=timezone.utc),
                path=focus_path,
            )
        )


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
        lambda name, *, window=None: (calls.append((name, window)) or SimpleNamespace(status="ready")),
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
