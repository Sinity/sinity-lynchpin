"""Machine telemetry promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .substrate_promote_status import (
    SOURCE_MACHINE,
    SOURCE_MACHINE_EXPERIMENTS,
    SOURCE_MACHINE_GPU,
    SOURCE_MACHINE_NETWORK,
    SOURCE_MACHINE_SERVICE_STATE,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_machine_tables(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    from lynchpin.substrate.machine import (
        promote_machine_experiment_runs,
        promote_machine_gpu_samples,
        promote_machine_metric_samples,
        promote_machine_network_samples,
        promote_machine_service_states,
    )

    if selection.includes(
        SOURCE_MACHINE,
        SOURCE_MACHINE_SERVICE_STATE,
        SOURCE_MACHINE_GPU,
        SOURCE_MACHINE_NETWORK,
    ):
        try:
            from lynchpin.sources.machine import (
                gpu_samples,
                metric_samples,
                network_samples,
                readiness as machine_readiness,
                service_states,
            )

            machine_ready = machine_readiness()
            if selection.includes(SOURCE_MACHINE):
                live_count = promote_machine_metric_samples(
                    conn,
                    refresh_id=refresh_id,
                    samples=metric_samples(start=window_start, end=window_end),
                )
                counts["machine_metric_samples"] = live_count
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_MACHINE,
                    status="ok"
                    if live_count
                    else (
                        "unavailable"
                        if machine_ready.status == "unavailable"
                        else "empty"
                    ),
                    reason=machine_ready.reason,
                    row_count=live_count,
                    window_start=window_start,
                    window_end=window_end,
                )
            if selection.includes(SOURCE_MACHINE_SERVICE_STATE):
                service_count = promote_machine_service_states(
                    conn,
                    refresh_id=refresh_id,
                    states=service_states(start=window_start, end=window_end),
                )
                counts["machine_service_states"] = service_count
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_MACHINE_SERVICE_STATE,
                    status="ok"
                    if service_count
                    else (
                        "unavailable"
                        if machine_ready.status == "unavailable"
                        else "empty"
                    ),
                    reason=machine_ready.reason,
                    row_count=service_count,
                    window_start=window_start,
                    window_end=window_end,
                )
            if selection.includes(SOURCE_MACHINE_GPU):
                gpu_count = promote_machine_gpu_samples(
                    conn,
                    refresh_id=refresh_id,
                    samples=gpu_samples(start=window_start, end=window_end),
                )
                counts["machine_gpu_samples"] = gpu_count
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_MACHINE_GPU,
                    status="ok"
                    if gpu_count
                    else (
                        "unavailable"
                        if machine_ready.status == "unavailable"
                        else "empty"
                    ),
                    reason=machine_ready.reason,
                    row_count=gpu_count,
                    window_start=window_start,
                    window_end=window_end,
                )
            if selection.includes(SOURCE_MACHINE_NETWORK):
                network_count = promote_machine_network_samples(
                    conn,
                    refresh_id=refresh_id,
                    samples=network_samples(start=window_start, end=window_end),
                )
                counts["machine_network_samples"] = network_count
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_MACHINE_NETWORK,
                    status="ok"
                    if network_count
                    else (
                        "unavailable"
                        if machine_ready.status == "unavailable"
                        else "empty"
                    ),
                    reason=machine_ready.reason,
                    row_count=network_count,
                    window_start=window_start,
                    window_end=window_end,
                )
        except Exception as exc:
            log.warning(
                "substrate_promote: machine telemetry promotion skipped: %s", exc
            )
            for source in (
                SOURCE_MACHINE,
                SOURCE_MACHINE_SERVICE_STATE,
                SOURCE_MACHINE_GPU,
                SOURCE_MACHINE_NETWORK,
            ):
                if selection.includes(source):
                    record_source_status(
                        conn,
                        refresh_id=refresh_id,
                        source=source,
                        status="error",
                        reason=str(exc),
                        row_count=0,
                        window_start=window_start,
                        window_end=window_end,
                    )

    if not selection.includes(SOURCE_MACHINE_EXPERIMENTS):
        return

    try:
        from lynchpin.sources.machine_experiments import (
            experiment_root,
            experiment_runs,
        )

        exp_root = experiment_root()
        runs = list(experiment_runs(start=window_start, end=window_end))
        run_count = promote_machine_experiment_runs(
            conn,
            refresh_id=refresh_id,
            runs=runs,
        )
        counts["machine_experiment_runs"] = run_count
        exp_reason: str | None
        if run_count:
            status = "ok"
            exp_reason = None
        elif exp_root.exists():
            status = "empty"
            exp_reason = "no machine experiment manifests in window"
        else:
            status = "unavailable"
            exp_reason = f"machine experiment root not found at {exp_root}"
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_EXPERIMENTS,
            status=status,
            reason=exp_reason,
            row_count=run_count,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: machine experiment promotion skipped: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_MACHINE_EXPERIMENTS,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )
