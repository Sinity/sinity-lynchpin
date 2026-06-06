from __future__ import annotations

import json
from datetime import date

from lynchpin.ingest.irc_materialize import IRC_EVENTS_SCHEMA_VERSION, materialize_irc_events


def test_materialize_irc_events_records_input_high_water(monkeypatch, tmp_path):
    raw_root = tmp_path / "_raw"
    log = raw_root / "#chan" / "2026-01-01.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-01-01 10:00:00\talice\thello\n", encoding="utf-8")
    output = tmp_path / "processed" / "events.ndjson"
    manifest_path = output.with_suffix(".manifest.json")

    monkeypatch.setattr("lynchpin.ingest.irc_materialize.irc_manifest_path", lambda: manifest_path)

    manifest = materialize_irc_events(root=raw_root, output=output)

    assert manifest["row_count"] == 1
    assert manifest["schema_version"] == IRC_EVENTS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
    assert manifest["date_boundary"] == "logical_06:00_local"
    assert manifest["first_timestamp_date"] == "2026-01-01"
    assert manifest["last_timestamp_date"] == "2026-01-01"
    assert manifest_path.exists()


def test_materialize_irc_events_records_logical_date_bounds(monkeypatch, tmp_path):
    raw_root = tmp_path / "_raw"
    log = raw_root / "#chan" / "2026-06-06.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-06-06 01:00:00\talice\tlate hello\n", encoding="utf-8")
    output = tmp_path / "processed" / "events.ndjson"
    manifest_path = output.with_suffix(".manifest.json")

    monkeypatch.setattr("lynchpin.ingest.irc_materialize.irc_manifest_path", lambda: manifest_path)

    manifest = materialize_irc_events(root=raw_root, output=output)

    assert manifest["first_timestamp_date"] == "2026-06-06"
    assert manifest["last_timestamp_date"] == "2026-06-06"
    assert manifest["first_date"] == "2026-06-05"
    assert manifest["last_date"] == "2026-06-05"


def test_materialize_irc_events_merges_requested_window(monkeypatch, tmp_path):
    raw_root = tmp_path / "_raw"
    log = raw_root / "#chan" / "2026-06-06.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-06-06 10:00:00\tbob\tnew window\n", encoding="utf-8")
    output = tmp_path / "processed" / "events.ndjson"
    manifest_path = output.with_suffix(".manifest.json")
    output.parent.mkdir(parents=True)
    output.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-05T10:00:00+00:00",
                        "speaker_raw": "alice",
                        "speaker_canonical": "alice",
                        "text": "before",
                        "channel": "#chan",
                        "source_file": "old.log",
                        "line_no": 1,
                        "is_meta": False,
                        "word_count": 1,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-06T10:00:00+00:00",
                        "speaker_raw": "alice",
                        "speaker_canonical": "alice",
                        "text": "old window",
                        "channel": "#chan",
                        "source_file": "old.log",
                        "line_no": 2,
                        "is_meta": False,
                        "word_count": 2,
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-07T10:00:00+00:00",
                        "speaker_raw": "alice",
                        "speaker_canonical": "alice",
                        "text": "after",
                        "channel": "#chan",
                        "source_file": "old.log",
                        "line_no": 3,
                        "is_meta": False,
                        "word_count": 1,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "covered_dates": ["2026-06-05", "2026-06-06", "2026-06-07"],
                "first_date": "2026-06-05",
                "last_date": "2026-06-07",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.ingest.irc_materialize.irc_manifest_path", lambda: manifest_path)

    manifest = materialize_irc_events(
        root=raw_root,
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["text"] for row in rows] == ["before", "new window", "after"]
    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["window_start"] == "2026-06-06"
    assert manifest["window_end"] == "2026-06-07"
