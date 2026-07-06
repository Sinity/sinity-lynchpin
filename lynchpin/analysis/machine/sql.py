"""Shared SQL fragments for machine analysis read models."""

from __future__ import annotations


def latest_machine_rows(table: str) -> str:
    keys = {
        "machine_metric_sample": "observed_at, host, source",
        "machine_gpu_sample": "observed_at, host, source",
        "machine_service_state": "observed_at, host, scope, unit",
        "machine_network_sample": "observed_at, host, interface",
        "machine_cgroup_memory_sample": "observed_at, host, label",
        # host + source_row_id (the live SQLite table's own autoincrement id)
        # rather than (observed_at, host, killer, victim_pid): earlyoom emits
        # repeated escalating SIGTERM warnings against the same victim pid
        # within the same observed_at second, so that tuple is not unique.
        "machine_kill_event": "host, source_row_id",
        "machine_process_io_delta_sample": "observed_at, host, pid, process_start_time_ticks",
        "machine_process_memory_sample": "observed_at, host, pid, process_start_time_ticks",
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
