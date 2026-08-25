import json
from datetime import date

import pytest

from lynchpin.graph.context_pack import _validate_incremental_relation_inputs
from lynchpin.graph.evidence_sources import _run_source
from lynchpin.graph.performance import GraphStageRecorder, recorded_stage
from lynchpin.core.evidence_graph import EvidenceNode


def test_stage_receipts_are_durable_and_keep_last_completed_stage(tmp_path):
    path = tmp_path / "stage-receipts.ndjson"
    recorder = GraphStageRecorder(
        attempt_id="attempt-1", refresh_id="refresh-1", path=path
    )
    with recorded_stage(
        recorder,
        "source:git",
        window_start=date(2026, 8, 1),
        window_end=date(2026, 8, 3),
        node_count=lambda: 2,
        edge_count=lambda: 1,
    ):
        pass
    with pytest.raises(RuntimeError):
        with recorded_stage(
            recorder,
            "edge_family:temporal_overlap",
            window_start=date(2026, 8, 1),
            window_end=date(2026, 8, 3),
            node_count=lambda: 2,
            edge_count=lambda: 1,
        ):
            raise RuntimeError("later stage failed")

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["started", "completed", "started", "failed"]
    assert rows[1]["stage"] == "source:git"
    assert rows[1]["window_start"] == "2026-08-01"
    assert rows[1]["window_end"] == "2026-08-03"
    assert rows[1]["attempt_id"] == rows[3]["attempt_id"] == "attempt-1"
    assert rows[3]["status"] == "failed"
    assert recorder.records[1]["stage"] == "source:git"


def test_source_receipt_survives_source_failure_and_records_delta(tmp_path):
    recorder = GraphStageRecorder(
        attempt_id="attempt-2", refresh_id="refresh-2", path=tmp_path / "receipts.ndjson"
    )
    nodes = []
    edges = []
    caveats = []

    def build():
        nodes.append(object())
        raise ValueError("source unavailable")

    _run_source(
        "broken",
        caveats,
        build,
        node_count=lambda: len(nodes),
        edge_count=lambda: len(edges),
        recorder=recorder,
        window_start=date(2026, 8, 1),
        window_end=date(2026, 8, 1),
    )
    terminal = recorder.records[-1]
    assert terminal["status"] == "failed"
    assert terminal["node_delta"] == 1
    assert "source unavailable" in terminal["error"]


def test_incremental_relation_inputs_reject_full_predecessor():
    def node(identifier, day):
        return EvidenceNode(identifier, "raw_log", "raw_log", day, None, identifier)

    tail = node("tail", date(2026, 8, 3))
    boundary = node("boundary", date(2026, 8, 2))
    predecessor = node("old", date(2026, 7, 1))
    with pytest.raises(ValueError, match="outside tail/boundary"):
        _validate_incremental_relation_inputs(
            relation_nodes=(boundary, tail, predecessor),
            boundary_nodes=(boundary,),
            tail_nodes=(tail,),
        )
