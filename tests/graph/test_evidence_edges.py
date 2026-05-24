import json
from datetime import date, datetime, timezone

import pytest

from lynchpin.core.evidence_graph import EvidenceNode
from lynchpin.graph.evidence_edges import load_symbol_changes_index, temporal_overlap_edges


def _reload_config(monkeypatch) -> None:
    import lynchpin.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "_CONFIG", None, raising=False)


def test_load_symbol_changes_index_requires_materialized_product(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LYNCHPIN_ANALYSIS_OUTPUT_DIR", str(tmp_path / "analysis"))
    _reload_config(monkeypatch)

    with pytest.raises(FileNotFoundError, match="active symbol-change product"):
        load_symbol_changes_index()


def test_load_symbol_changes_index_accepts_valid_empty_product(tmp_path, monkeypatch):
    monkeypatch.setenv("LYNCHPIN_ANALYSIS_OUTPUT_DIR", str(tmp_path / "analysis"))
    _reload_config(monkeypatch)
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "active_symbol_changes.json").write_text(
        json.dumps({"events": []}),
        encoding="utf-8",
    )

    assert load_symbol_changes_index() == {}


def test_temporal_overlap_edges_group_by_project_and_stop_at_interval_end() -> None:
    day = date(2026, 5, 24)

    def node(
        node_id: str,
        source: str,
        project: str,
        start_minute: int,
        end_minute: int,
    ) -> EvidenceNode:
        return EvidenceNode(
            id=node_id,
            kind="terminal_session",
            source=source,
            date=day,
            project=project,
            summary=node_id,
            start=datetime(2026, 5, 24, 10, start_minute, tzinfo=timezone.utc),
            end=datetime(2026, 5, 24, 10, end_minute, tzinfo=timezone.utc),
        )

    edges = temporal_overlap_edges(
        [
            node("git-a", "git", "lynchpin", 0, 20),
            node("ai-a", "polylogue", "lynchpin", 10, 25),
            node("term-a", "terminal", "lynchpin", 25, 30),
            node("git-b", "git", "polylogue", 10, 15),
            node("ai-b", "polylogue", "polylogue", 16, 20),
        ]
    )

    assert [(edge.source_id, edge.target_id, edge.relation) for edge in edges] == [
        ("git-a", "ai-a", "temporal_overlap")
    ]
