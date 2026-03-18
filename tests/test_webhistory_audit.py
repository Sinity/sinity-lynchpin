from __future__ import annotations

import json
from datetime import datetime, timezone

from lynchpin.ingest.webhistory import _audit_webhistory


def test_audit_matches_simulated_dedup_against_canonical_and_merged(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    canonical_dir = tmp_path / "canonical"
    raw_dir.mkdir()
    canonical_dir.mkdir()

    raw_recent = raw_dir / "a_history.jsonl"
    raw_recent.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "url": "https://example.com/path?utm_source=test",
                        "title": "Example",
                        "visit_time": "2026-03-17T10:00:00+00:00",
                    }
                ),
                json.dumps(
                    {
                        "url": "https://example.com/path",
                        "title": "Example duplicate",
                        "visit_time": "2026-03-17T10:00:03+00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raw_older = raw_dir / "b_history.json"
    raw_older.write_text(
        json.dumps(
            [
                {
                    "url": "https://example.com/other",
                    "title": "Other",
                    "visitTime": 1773720000000,
                }
            ]
        ),
        encoding="utf-8",
    )

    (canonical_dir / "a_history_unique.jsonl").write_text(
        json.dumps(
            {
                "url": "https://example.com/path?utm_source=test",
                "title": "Example",
                "visit_time": "2026-03-17T10:00:00+00:00",
                "_source_file": "a_history.jsonl",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (canonical_dir / "b_history_unique.json").write_text(
        json.dumps(
            [
                {
                    "url": "https://example.com/other",
                    "title": "Other",
                    "visitTime": 1773720000000,
                    "_source_file": "b_history.json",
                }
            ]
        ),
        encoding="utf-8",
    )

    merged = tmp_path / "full_history.ndjson"
    merged.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "url": "https://example.com/path?utm_source=test",
                        "title": "Example",
                        "norm": "https://example.com/path",
                        "source": "a_history_unique.jsonl",
                        "iso_time": "2026-03-17T10:00:00+00:00",
                    }
                ),
                json.dumps(
                    {
                        "url": "https://example.com/other",
                        "title": "Other",
                        "norm": "https://example.com/other",
                        "source": "b_history_unique.json",
                        "iso_time": datetime.fromtimestamp(1773720000, tz=timezone.utc).isoformat(),
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = _audit_webhistory(
        raw_root=raw_dir,
        canonical=canonical_dir,
        merged=merged,
        tolerance=5,
        sample=10,
    )

    assert report["raw_rows"] == 3
    assert report["simulated_dedup_count"] == 2
    assert report["simulated_duplicate_rows"] == 1
    assert report["canonical_count"] == 2
    assert report["merged_count"] == 2
    assert report["canonical_duplicate_keys"] == 0
    assert report["merged_duplicate_keys"] == 0
    assert report["expected_vs_canonical"]["missing"] == 0
    assert report["expected_vs_canonical"]["extra"] == 0
    assert report["canonical_vs_merged"]["missing"] == 0
    assert report["canonical_vs_merged"]["extra"] == 0
