"""Tests for lynchpin.sources.indices.session_summaries."""

from __future__ import annotations

import json

from lynchpin.sources.indices.session_summaries import (
    _provider_from_source_path,
    iter_session_summaries_from,
)


def test_iter_session_summaries_from_parses_level1_json(tmp_path) -> None:
    payload = {
        "source_path": "/realm/data/exports/chatlog/processed/markdown/codex/example/conversation.md",
        "title": "Trajectory cutover",
        "timeframe": "2026-03-18",
        "summary": "Converged the read-model surface.",
        "highlights": ["Removed bridge drift", "Added warehouse ingestion"],
        "decisions": ["Keep lynchpin throwaway"],
        "follow_ups": ["Port the same understanding into Sinex later"],
        "action_items": [{"task": "update docs", "status": "done"}],
        "risks": ["Polylogue coverage still depends on renders"],
        "raw_references": ["/realm/project/sinity-lynchpin/docs/plans/sinex-integration.md"],
    }
    summary_path = tmp_path / "trajectory-cutover.json"
    summary_path.write_text(json.dumps(payload), encoding="utf-8")

    records = list(iter_session_summaries_from(tmp_path))

    assert len(records) == 1
    record = records[0]
    assert record.summary_path == summary_path
    assert record.provider == "codex"
    assert record.title == "Trajectory cutover"
    assert record.highlights == ["Removed bridge drift", "Added warehouse ingestion"]
    assert record.decisions == ["Keep lynchpin throwaway"]
    assert record.follow_ups == ["Port the same understanding into Sinex later"]
    assert record.action_items == [{"task": "update docs", "status": "done"}]
    assert record.risks == ["Polylogue coverage still depends on renders"]
    assert record.raw_references == ["/realm/project/sinity-lynchpin/docs/plans/sinex-integration.md"]
    assert record.generated_at.tzinfo is not None


def test_iter_session_summaries_from_skips_invalid_json(tmp_path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert list(iter_session_summaries_from(tmp_path)) == []


def test_provider_from_source_path_uses_polylogue_layout() -> None:
    source_path = "/realm/data/exports/chatlog/processed/markdown/claude-code/2026/03/18/conversation.md"
    assert _provider_from_source_path(source_path) == "claude-code"


def test_provider_from_source_path_falls_back_to_unknown() -> None:
    assert _provider_from_source_path("/tmp/random.txt") == "unknown"
