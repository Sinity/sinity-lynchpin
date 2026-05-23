from __future__ import annotations

import json
from datetime import date

from lynchpin.ingest.webhistory import build_full_history, full_history_manifest_path
from lynchpin.materialization import MaterializedDataset, render_materialization_audit


def test_build_full_history_writes_manifest(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    segment = data_dir / "segment_unique_2026-01-01_to_2026-01-01.ndjson"
    segment.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "iso_time": "2026-01-01T10:00:00+00:00",
                        "url": "https://example.com/a",
                        "title": "A",
                        "source": "fixture",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-01T10:01:00+00:00",
                        "url": "https://example.com/b",
                        "title": "B",
                        "source": "fixture",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "derived" / "full_history.ndjson"
    report = build_full_history(data_dir=data_dir, output=output)

    manifest_path = full_history_manifest_path(output)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["row_count"] == 2
    assert output.exists()
    assert manifest["dataset"] == "webhistory.full_history"
    assert manifest["row_count"] == 2
    assert manifest["first_visit_at"].startswith("2026-01-01T10:00:00")
    assert manifest["last_visit_at"].startswith("2026-01-01T10:01:00")


def test_render_materialization_audit_marks_non_ready_reasons(tmp_path):
    row = MaterializedDataset(
        name="example",
        status="partial",
        authority="raw fixture",
        query_surface="lynchpin.sources.example",
        materialized_paths=(tmp_path / "example.ndjson",),
        raw_roots=(tmp_path / "raw",),
        row_count=10,
        first_date=date(2020, 1, 1),
        last_date=date(2020, 1, 2),
        refresh_command="example refresh",
        reason="canonical product missing",
    )

    rendered = render_materialization_audit([row])

    assert "| example | partial | 10 | 2020-01-01 -> 2020-01-02 |" in rendered
    assert "canonical product missing" in rendered
