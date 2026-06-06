"""Canonical mapping from substrate tables to source-status names."""

from __future__ import annotations

SUBSTRATE_TABLE_SOURCE: dict[str, str] = {
    "commit_fact": "commits",
    "file_change_fact": "file_changes",
    "symbol_change": "symbols",
    "evidence_node": "evidence_graph",
    "evidence_edge": "evidence_graph",
    "ai_work_event": "ai_attribution",
    "work_observation": "work_observations",
    "work_observation_stage": "work_observations",
    "work_observation_test_result": "work_observations",
    "machine_metric_sample": "machine",
    "machine_gpu_sample": "machine_gpu_sample",
    "machine_network_sample": "machine_network_sample",
    "machine_service_state": "machine_service_state",
    "machine_experiment_run": "machine_experiments",
}


def source_for_substrate_table(table: str) -> str:
    """Return substrate_source_status.source for a table, falling back to table."""

    return SUBSTRATE_TABLE_SOURCE.get(table, table)


__all__ = ["SUBSTRATE_TABLE_SOURCE", "source_for_substrate_table"]
