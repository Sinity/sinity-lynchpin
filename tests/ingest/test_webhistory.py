from __future__ import annotations

import json

from lynchpin.ingest import webhistory


def test_build_full_history_streams_deduplicated_rows_in_order(monkeypatch, tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    segment = data_dir / "browser_unique_2026-01-01_to_2026-01-01.ndjson"
    segment.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "iso_time": "2026-01-01T12:00:10+00:00",
                        "url": "https://example.test/a",
                        "title": "Duplicate",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-01T11:00:00+00:00",
                        "url": "https://example.test/b",
                        "title": "Earlier",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-01T12:00:00+00:00",
                        "url": "https://example.test/a",
                        "title": "First",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "derived" / "full_history.ndjson"
    real_atomic_write_ndjson = webhistory.atomic_write_ndjson
    received_rows = []

    def capture_rows(path, rows, *, dumps=None) -> None:
        received_rows.append(rows)
        real_atomic_write_ndjson(path, rows, dumps=dumps)

    monkeypatch.setattr(webhistory, "atomic_write_ndjson", capture_rows)

    report = webhistory.build_full_history(data_dir=data_dir, output=output)

    assert len(received_rows) == 1
    assert not isinstance(received_rows[0], list)
    assert report["row_count"] == 2
    assert report["duplicate_count"] == 1
    assert output.read_text(encoding="utf-8") == (
        json.dumps(
            {
                "url": "https://example.test/b",
                "title": "Earlier",
                "norm": "https://example.test/b",
                "source": str(segment),
                "iso_time": "2026-01-01T11:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "url": "https://example.test/a",
                "title": "First",
                "norm": "https://example.test/a",
                "source": str(segment),
                "iso_time": "2026-01-01T12:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def test_build_full_history_publishes_empty_output(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    output = tmp_path / "derived" / "full_history.ndjson"
    output.parent.mkdir()
    output.write_text("stale\n", encoding="utf-8")

    report = webhistory.build_full_history(data_dir=data_dir, output=output)

    assert report["row_count"] == 0
    assert output.read_text(encoding="utf-8") == ""
