from __future__ import annotations

from lynchpin.core.substrate_sources import source_for_substrate_table


def test_source_for_substrate_table_maps_known_tables() -> None:
    assert source_for_substrate_table("commit_fact") == "commits"
    assert source_for_substrate_table("machine_experiment_run") == "machine_experiments"
    assert source_for_substrate_table("evidence_node") == "evidence_graph"


def test_source_for_substrate_table_falls_back_to_table_name() -> None:
    assert source_for_substrate_table("analysis_claim") == "analysis_claim"
