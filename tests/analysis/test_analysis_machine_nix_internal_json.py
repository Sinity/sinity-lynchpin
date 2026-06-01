from __future__ import annotations

import json
from datetime import datetime, timezone


def test_summarize_internal_json_tolerates_mixed_nix_lines(tmp_path):
    from lynchpin.analysis.machine.nix_internal_json import summarize_internal_json

    path = tmp_path / "internal.ndjson"
    path.write_text(
        "\n".join([
            json.dumps({"action": "start", "id": 1, "type": "build", "level": "info", "timestamp": "2026-05-01T12:00:00+00:00"}),
            json.dumps({"action": "result", "activity": 1, "type": "done", "ts": 1777636801}),
            json.dumps({"action": "stop", "id": 1, "timestamp": "2026-05-01T12:00:02+00:00"}),
            "{malformed",
        ]),
        encoding="utf-8",
    )

    summary = summarize_internal_json(path)

    assert summary.exists is True
    assert summary.line_count == 4
    assert summary.parsed_count == 3
    assert summary.malformed_count == 1
    assert summary.activity_count == 1
    assert summary.result_type_counts["start"] == 1
    assert summary.result_type_counts["result"] == 1
    assert summary.first_timestamp == datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    assert summary.phase_count == 1
    assert summary.phases[0].activity_id == "1"
    assert summary.phases[0].duration_seconds == 2.0
    assert summary.phases[0].result_type_counts["done"] == 1
    assert summary.phases[0].status == "complete"
    assert any("malformed" in caveat for caveat in summary.caveats)


def test_summarize_internal_json_accepts_nix_cli_prefix_without_timestamps(tmp_path):
    from lynchpin.analysis.machine.nix_internal_json import summarize_internal_json

    path = tmp_path / "prefixed.ndjson"
    path.write_text(
        "\n".join([
            '@nix {"action":"start","id":1,"level":6,"parent":0,"text":"querying info","type":0}',
            '@nix {"action":"stop","id":1}',
        ]),
        encoding="utf-8",
    )

    summary = summarize_internal_json(path)

    assert summary.line_count == 2
    assert summary.parsed_count == 2
    assert summary.malformed_count == 0
    assert summary.activity_count == 1
    assert summary.phase_count == 1
    assert summary.phases[0].status == "partial"
    assert "internal-json capture has no parseable timestamps" in summary.caveats


def test_summarize_internal_json_reports_missing_capture(tmp_path):
    from lynchpin.analysis.machine.nix_internal_json import summarize_internal_json

    summary = summarize_internal_json(tmp_path / "missing.ndjson")

    assert summary.exists is False
    assert summary.parsed_count == 0
    assert summary.phase_count == 0
    assert "does not exist" in summary.caveats[0]
