from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module

import pytest

from lynchpin.core.evidence_graph import EvidenceGraph
from lynchpin.graph import evidence_graph
from lynchpin.substrate.connection import CandidateGenerationRejected, _substrate_path_override


def _graph() -> EvidenceGraph:
    return EvidenceGraph(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        generated_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )


def test_cli_current_state_refuses_graph_promotion_without_candidate(monkeypatch) -> None:
    from lynchpin.cli.current_state import render_current_state

    context_pack = import_module("lynchpin.graph.context_pack")
    monkeypatch.setattr(context_pack, "build_evidence_graph", lambda **_kwargs: _graph())

    with pytest.raises(CandidateGenerationRejected, match="active candidate generation"):
        render_current_state(
            start=date(2026, 5, 1), end=date(2026, 5, 2), materialize_substrate=True,
        )


def test_graph_promotion_keeps_best_effort_write_failure_inside_candidate(monkeypatch, tmp_path, caplog) -> None:
    import lynchpin.substrate as substrate

    def fail_connect(*_args, **_kwargs):
        raise RuntimeError("synthetic write failure")

    monkeypatch.setattr(substrate, "connect", fail_connect)
    token = _substrate_path_override.set(tmp_path / "candidate.duckdb")
    try:
        evidence_graph.promote_graph_to_substrate(_graph(), refresh_id="synthetic")
    finally:
        _substrate_path_override.reset(token)

    assert "synthetic write failure" in caplog.text
