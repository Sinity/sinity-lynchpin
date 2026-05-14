"""Concrete observability input catalog.

This is deliberately small and operational. It does not try to model every
piece of personal data in Lynchpin; it records the machine/performance surfaces
that can otherwise accrete into overlapping dashboards and ambiguous datasets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..core.config import get_config

ObservabilityLayer = Literal[
    "raw_capture",
    "native_ledger",
    "derived_substrate",
    "operational_view",
    "legacy_backfill",
]

IntegrationState = Literal[
    "canonical",
    "promote_pending",
    "reference_only",
    "retire_after_verified_backup",
    "derived",
]


@dataclass(frozen=True)
class ObservabilityInput:
    id: str
    owner: str
    layer: ObservabilityLayer
    integration_state: IntegrationState
    path: Path | None
    substrate_table: str | None
    grain: str
    state_dimensions: tuple[str, ...]
    retention: str
    next_action: str

    @property
    def exists(self) -> bool | None:
        if self.path is None:
            return None
        return self.path.exists()


def observability_inputs() -> tuple[ObservabilityInput, ...]:
    cfg = get_config()
    return (
        ObservabilityInput(
            id="machine.telemetry",
            owner="sinnix",
            layer="raw_capture",
            integration_state="canonical",
            path=cfg.machine_telemetry_db,
            substrate_table="machine_metric_sample; machine_service_state; machine_network_sample",
            grain="10s host sample, 60s service-state sample, and 5m network-link sample",
            state_dimensions=(
                "cpu_power",
                "thermal",
                "gpu_power_link",
                "psi",
                "scheduler_latency_sentinel",
                "d_state_tasks",
                "service_state",
                "network_link_quality",
            ),
            retention="canonical raw capture under /realm/data/captures/machine",
            next_action="promote live metric, service, and network samples on ordinary Lynchpin refresh; join experiment windows by observed_at",
        ),
        ObservabilityInput(
            id="machine.below",
            owner="sinnix",
            layer="operational_view",
            integration_state="reference_only",
            path=Path("/var/log/below/store"),
            substrate_table=None,
            grain="5s cgroup/process/system history",
            state_dimensions=("per_process_io", "per_cgroup_cpu_memory_io", "time_travel_debug"),
            retention="short operational retention managed by below-prune",
            next_action="do not promote wholesale; export bounded windows when an experiment or incident requires process attribution",
        ),
        ObservabilityInput(
            id="machine.sinnix_observe",
            owner="sinnix",
            layer="operational_view",
            integration_state="derived",
            path=cfg.sinnix_root / "scripts/sinnix-observe",
            substrate_table=None,
            grain="on-demand report window",
            state_dimensions=("pressure_snapshot", "systemd_state", "below_window", "project_ledgers"),
            retention="not a canonical dataset",
            next_action="keep as operator report; remove data ownership from it as machine/network/sinex/polylogue promote into Lynchpin",
        ),
        ObservabilityInput(
            id="sinex.self_observation",
            owner="sinex",
            layer="native_ledger",
            integration_state="promote_pending",
            path=Path("/realm/project/sinex"),
            substrate_table="sinex_telemetry_rollup",
            grain="Sinex event/telemetry aggregates",
            state_dimensions=("pipeline_latency", "node_health", "event_lag", "throughput", "pool_stats"),
            retention="Sinex-owned database/event substrate",
            next_action="read Sinex rollups through a Lynchpin source; do not duplicate Sinex internals in Sinnix",
        ),
        ObservabilityInput(
            id="polylogue.run_ledger",
            owner="polylogue",
            layer="native_ledger",
            integration_state="canonical",
            path=cfg.polylogue_db,
            substrate_table="ai_work_event",
            grain="conversation/session/work-event rows",
            state_dimensions=("agent_activity", "project_attribution", "run_cost", "work_event_kind"),
            retention="Polylogue-owned SQLite/archive plus Lynchpin substrate promotion",
            next_action="join to machine/service windows for agent-load experiments; keep capture ownership in Polylogue",
        ),
        ObservabilityInput(
            id="machine.experiment_run",
            owner="sinnix",
            layer="raw_capture",
            integration_state="canonical",
            path=cfg.machine_host_root / "experiments",
            substrate_table="machine_experiment_run",
            grain="one immutable workload invocation manifest",
            state_dimensions=("planned_treatment", "observed_treatment", "cache_profile", "service_profile", "workload_identity"),
            retention="canonical experiment manifests under machine telemetry root",
            next_action="use randomized manifests and telemetry joins for controlled benchmark matrices",
        ),
    )


def observability_input_by_id(input_id: str) -> ObservabilityInput:
    for item in observability_inputs():
        if item.id == input_id:
            return item
    raise KeyError(input_id)


__all__ = [
    "IntegrationState",
    "ObservabilityInput",
    "ObservabilityLayer",
    "observability_input_by_id",
    "observability_inputs",
]
