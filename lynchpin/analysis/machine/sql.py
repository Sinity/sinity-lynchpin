"""Shared SQL fragments for machine analysis read models."""

from __future__ import annotations


def latest_machine_rows(table: str) -> str:
    keys = {
        "machine_metric_sample": "observed_at, host, source",
        "machine_gpu_sample": "observed_at, host, source",
        "machine_service_state": "observed_at, host, scope, unit",
        "machine_network_sample": "observed_at, host, interface",
        "machine_experiment_run": "run_id",
        "work_observation": "source, source_id",
        "work_observation_stage": "source, source_id",
        "work_observation_test_result": "source, source_id",
    }[table]
    return f"""
        SELECT * EXCLUDE (_rn)
        FROM (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY {keys}
                    ORDER BY materialized_at DESC, refresh_id DESC
                ) AS _rn
            FROM {table}
        )
        WHERE _rn = 1
    """
