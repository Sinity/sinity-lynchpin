from __future__ import annotations

from datetime import date


def test_materialize_activity_content_records_input_high_water(monkeypatch, tmp_path):
    from lynchpin.ingest import activity_content_materialize
    from lynchpin.ingest.activity_content_materialize import ACTIVITY_CONTENT_SCHEMA_VERSION

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "activity_content_daily.ndjson"

    monkeypatch.setattr(activity_content_materialize, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(activity_content_materialize, "load_title_classification_map", lambda: {})
    focus_calls = []

    def fake_focus_spans(**kwargs):
        focus_calls.append(kwargs)
        return iter(())

    monkeypatch.setattr(activity_content_materialize, "focus_spans", fake_focus_spans)

    manifest = activity_content_materialize.materialize_activity_content(
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
        output=output,
    )

    assert manifest["row_count"] == 0
    assert manifest["schema_version"] == ACTIVITY_CONTENT_SCHEMA_VERSION
    assert manifest["input_file_count"] == 2
    assert manifest["input_latest_mtime"] is not None
    assert focus_calls
    assert all(call["enrich_polylogue"] is False for call in focus_calls)
