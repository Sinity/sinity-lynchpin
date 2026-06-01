"""Machine telemetry analysis surfaces."""

from .assumption_checks import analyze_machine_assumption_checks
from .attribution import analyze_below_attribution
from .attribution_claims import analyze_machine_attribution_claims
from .baselines import analyze_machine_observational_baselines
from .benchmark_manifest_bundle import analyze_machine_benchmark_manifest_bundle
from .benchmark_execution_queue import analyze_machine_benchmark_execution_queue
from .benchmark_plans import analyze_machine_benchmark_plans
from .benchmark_preflight import analyze_machine_benchmark_preflight
from .calibration import analyze_machine_calibration
from .below import analyze_below_exports
from .command_performance import analyze_command_performance
from .comparisons import analyze_machine_comparisons
from .devshell import analyze_devshell_performance
from .derivation_inventory import analyze_machine_derivation_inventory
from .context import analyze_machine_context_windows
from .dataset_diagnostics import analyze_machine_dataset_diagnostics
from .episodes import analyze_machine_episodes
from .experiment_manifest_diagnostics import analyze_machine_experiment_manifest_diagnostics
from .experiments import analyze_machine_experiment_claims
from .feature_frames import analyze_machine_feature_frames
from .instrumentation_gaps import analyze_machine_instrumentation_gaps
from .matched_designs import analyze_machine_matched_designs
from .measurement_system import analyze_machine_measurement_system
from .mechanisms import analyze_machine_mechanisms
from .mining import analyze_machine_mining
from .negative_controls import analyze_machine_negative_controls
from .observational import analyze_observational_command_deltas
from .readiness import analyze_machine_analysis_readiness
from .states import analyze_machine_work_states
from .support_assessment import analyze_machine_support_assessment
from .telemetry import analyze_machine_telemetry
from .validation_design import analyze_machine_validation_design

__all__ = [
    "analyze_below_exports",
    "analyze_machine_assumption_checks",
    "analyze_below_attribution",
    "analyze_machine_attribution_claims",
    "analyze_command_performance",
    "analyze_machine_benchmark_manifest_bundle",
    "analyze_machine_benchmark_execution_queue",
    "analyze_machine_benchmark_preflight",
    "analyze_machine_comparisons",
    "analyze_devshell_performance",
    "analyze_machine_derivation_inventory",
    "analyze_machine_benchmark_plans",
    "analyze_machine_calibration",
    "analyze_machine_observational_baselines",
    "analyze_machine_analysis_readiness",
    "analyze_machine_context_windows",
    "analyze_machine_dataset_diagnostics",
    "analyze_machine_episodes",
    "analyze_machine_experiment_manifest_diagnostics",
    "analyze_machine_experiment_claims",
    "analyze_machine_feature_frames",
    "analyze_machine_instrumentation_gaps",
    "analyze_machine_matched_designs",
    "analyze_machine_measurement_system",
    "analyze_machine_mechanisms",
    "analyze_machine_mining",
    "analyze_machine_negative_controls",
    "analyze_machine_work_states",
    "analyze_machine_support_assessment",
    "analyze_observational_command_deltas",
    "analyze_machine_telemetry",
    "analyze_machine_validation_design",
]
