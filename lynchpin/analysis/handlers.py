"""Closed handler identities for the analysis materialization DAG."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

_HANDLER_NAMES = (
    "write_machine_telemetry_analysis", "write_machine_episode_analysis", "write_machine_pressure_incidents", "write_stall_attribution", "write_below_analysis", "write_below_attribution_analysis", "write_below_export_handoff", "write_machine_context_analysis", "write_work_observation_analysis", "write_workflow_mechanics_report", "write_keylog_analysis", "write_machine_feature_frames", "write_machine_mining", "write_machine_dataset_diagnostics", "write_machine_validation_design", "write_machine_matched_designs", "write_machine_negative_controls", "write_machine_comparisons", "write_machine_work_state_analysis", "write_command_performance_analysis", "write_observational_command_deltas", "write_machine_attribution_candidates", "write_machine_derivation_inventory", "write_machine_benchmark_plans", "write_machine_benchmark_manifest_bundle", "write_machine_benchmark_preflight", "write_machine_support_assessment", "write_machine_benchmark_execution_handoff", "write_machine_mechanisms", "write_machine_instrumentation_gaps", "write_machine_calibration", "write_machine_measurement_system", "write_machine_attribution_claims", "write_machine_assumption_checks", "write_devshell_performance_analysis", "write_machine_observational_baselines", "write_machine_experiment_claims", "write_machine_experiment_manifest_diagnostics", "write_gap_summary_analysis", "write_machine_analysis_readiness", "write_anomaly_crossref_report", "write_life_phase_report", "write_productivity_predictors_report", "write_substance_health_report", "write_burnout_warning_report", "write_ai_session_efficiency_report", "write_quota_advisory", "_promote_operator_day", "write_ambient_intelligence", "run_active_project_snapshot", "run_active_code_inventory", "run_active_python_complexity", "run_active_python_import_graph", "run_active_rust_graph", "run_active_git_facts", "write_code_history_claims", "run_active_work_packages", "run_project_velocity_windows", "run_active_hotspots", "run_active_guardrails", "run_active_structural_findings", "run_active_semantic_static_findings", "run_active_rust_dependency_hygiene", "run_active_python_dependency_hygiene", "run_active_symbol_index", "run_active_symbol_diffs", "run_active_ci_health", "run_active_commit_semantics", "run_active_ai_attribution", "run_substrate_promote", "run_current_state_analysis", "_run_narrative", "run_active_github_frontier",
)


def _invoke(function: Callable[..., Any], args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> Any:
    return function(*args, **dict(kwargs))


class AnalysisHandlerRegistry:
    """Immutable registry assembled only from the checked-in handler table."""

    def __init__(self) -> None:
        from . import materialize

        functions = {name: getattr(materialize, name) for name in _HANDLER_NAMES}
        self._handlers = MappingProxyType(functions)

    def resolve(self, identity: str) -> Callable[..., Any]:
        try:
            return self._handlers[identity.removeprefix("analysis:")]
        except KeyError as exc:
            raise KeyError(f"unregistered analysis handler: {identity}") from exc


def handler_registry() -> AnalysisHandlerRegistry:
    return AnalysisHandlerRegistry()


__all__ = ["AnalysisHandlerRegistry", "handler_registry"]
