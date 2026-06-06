from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace


def test_materialize_activitywatch_derived_writes_graph_products(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod
    from lynchpin.ingest.activitywatch_derived_materialize import ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    from lynchpin.sources.activitywatch_derived import PRODUCT_KINDS

    start = datetime(2026, 6, 6, 8, tzinfo=timezone.utc)
    end = datetime(2026, 6, 6, 9, tzinfo=timezone.utc)
    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))
    monkeypatch.setattr(
        mod,
        "focus_spans",
        lambda **kwargs: (
            SimpleNamespace(
                start=start,
                end=end,
                kind="focused",
                app="kitty",
                title="lynchpin",
                mode="coding",
                project="lynchpin",
                duration_s=3600.0,
                keypress_count=3,
                keylog_state="available",
            ),
        ),
    )
    monkeypatch.setattr(
        mod,
        "project_focus_days",
        lambda **kwargs: (
            SimpleNamespace(date=date(2026, 6, 6), project="lynchpin", duration_s=3600.0),
        ),
    )
    monkeypatch.setattr(
        mod,
        "daily_activity",
        lambda **kwargs: (
            SimpleNamespace(
                date=date(2026, 6, 6),
                active_hours=1.0,
                deep_work_min=30.0,
                fragmentation_score=0.2,
                project_count=1,
                dominant_mode="coding",
                dominant_project="lynchpin",
                hourly_active=tuple([0.0] * 8 + [60.0] + [0.0] * 15),
                outage_hours=0.0,
                presence_active_hours=1.0,
                presence_typing_hours=0.5,
                presence_data_gap_hours=0.0,
            ),
        ),
    )
    monkeypatch.setattr(mod, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(mod, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(mod, "loops", lambda **kwargs: ())
    monkeypatch.setattr(mod, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(mod, "attention", lambda **kwargs: ())

    manifest = mod.materialize_activitywatch_derived(
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
        root=tmp_path,
    )

    assert set(manifest["product_paths"]) == set(PRODUCT_KINDS)
    assert manifest["schema_version"] == ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    assert manifest["row_counts"]["focus_spans"] == 1
    assert manifest["row_counts"]["project_focus_days"] == 1
    assert manifest["row_counts"]["daily_activity"] == 1
    assert manifest["row_count"] == 3
    assert manifest["window_semantics"] == "start inclusive, end exclusive"
    assert manifest["covered_dates"] == ["2026-06-06"]
    assert manifest["covered_date_count"] == 1
    assert (tmp_path / "activitywatch/graph/focus_spans.ndjson").exists()
    assert (tmp_path / "activitywatch/graph/manifest.json").exists()


def test_materialize_activitywatch_derived_replaces_only_requested_window(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    old_path = tmp_path / "activitywatch/graph/project_focus_days.ndjson"
    old_path.parent.mkdir(parents=True)
    old_path.write_text(
        "\n".join(
            [
                '{"date": "2026-06-05", "duration_s": 111.0, "project": "old"}',
                '{"date": "2026-06-06", "duration_s": 222.0, "project": "replace"}',
                '{"date": "2026-06-07", "duration_s": 333.0, "project": "future"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))
    monkeypatch.setattr(mod, "focus_spans", lambda **kwargs: ())
    monkeypatch.setattr(
        mod,
        "project_focus_days",
        lambda **kwargs: (
            SimpleNamespace(date=date(2026, 6, 6), project="new", duration_s=444.0),
        ),
    )
    monkeypatch.setattr(mod, "daily_activity", lambda **kwargs: ())
    monkeypatch.setattr(mod, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(mod, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(mod, "loops", lambda **kwargs: ())
    monkeypatch.setattr(mod, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(mod, "attention", lambda **kwargs: ())

    mod.materialize_activitywatch_derived(
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
        root=tmp_path,
    )

    rows = [
        line for line in old_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 3
    assert any('"project": "old"' in row for row in rows)
    assert any('"project": "new"' in row for row in rows)
    assert any('"project": "future"' in row for row in rows)
    assert not any('"project": "replace"' in row for row in rows)


def test_materialize_activitywatch_derived_preserves_sparse_covered_dates(monkeypatch, tmp_path):
    from lynchpin.ingest import activitywatch_derived_materialize as mod

    manifest_path = tmp_path / "activitywatch/graph/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        '{"covered_dates": ["2026-06-05", "2026-06-07"], "first_date": "2026-06-05", "last_date": "2026-06-07"}\n',
        encoding="utf-8",
    )
    canonical = tmp_path / "events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(mod, "activitywatch_derived_input_files", lambda: (canonical,))
    monkeypatch.setattr(mod, "focus_spans", lambda **kwargs: ())
    monkeypatch.setattr(mod, "project_focus_days", lambda **kwargs: ())
    monkeypatch.setattr(mod, "daily_activity", lambda **kwargs: ())
    monkeypatch.setattr(mod, "deep_work", lambda **kwargs: ())
    monkeypatch.setattr(mod, "circadian", lambda **kwargs: ())
    monkeypatch.setattr(mod, "loops", lambda **kwargs: ())
    monkeypatch.setattr(mod, "fragmentation", lambda **kwargs: ())
    monkeypatch.setattr(mod, "attention", lambda **kwargs: ())

    manifest = mod.materialize_activitywatch_derived(
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
        root=tmp_path,
    )

    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["first_date"] == "2026-06-05"
    assert manifest["last_date"] == "2026-06-07"
    assert manifest["row_count"] == 0
