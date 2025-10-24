"""Machine-readable status rollup for the analysis surface."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .canonical import load_analysis_spec
from ...core.io import load_json_if_exists, save_json
from ...core.config import get_config
from ...sources.analysis_artifacts import AnalysisArtifact, artifact_inventory

_DEFAULT_SPEC = Path(__file__).resolve().parents[1] / "analysis_spec.json"

MACHINE_ANALYSIS_ARTIFACTS = (
    "machine_telemetry_analysis.json",
    "machine_episode_analysis.json",
    "machine_below_analysis.json",
    "machine_below_attribution.json",
    "machine_context_windows.json",
    "machine_work_state_windows.json",
    "machine_work_observations.json",
    "machine_analysis_feature_frames.json",
    "machine_mining.json",
    "machine_dataset_diagnostics.json",
    "machine_validation_design.json",
    "machine_matched_designs.json",
    "machine_negative_controls.json",
    "machine_comparisons.json",
    "command_performance_windows.json",
    "machine_observational_deltas.json",
    "machine_attribution_candidates.json",
    "machine_derivation_inventory.json",
    "machine_benchmark_plans.json",
    "machine_benchmark_manifest_bundle.json",
    "machine_benchmark_preflight.json",
    "machine_benchmark_execution_handoff.json",
    "machine_below_export_handoff.json",
    "machine_experiment_manifest_diagnostics.json",
    "machine_support_assessment.json",
    "machine_mechanism_hypotheses.json",
    "machine_instrumentation_gaps.json",
    "machine_calibration_fixtures.json",
    "machine_measurement_system.json",
    "machine_attribution_claims.json",
    "machine_assumption_checks.json",
    "devshell_performance.json",
    "machine_observational_baselines.json",
    "machine_experiment_claims.json",
    "machine_gap_summary.json",
    "machine_analysis_readiness.json",
    "machine_analysis_materialization_report.json",
)


def _row(status: str, rationale: str, artifacts: list[str]) -> dict[str, object]:
    return {
        "status": status,
        "rationale": rationale,
        "artifacts": artifacts,
    }


def build_analysis_status(*, spec_path: str | Path | None = None) -> dict[str, object]:
    load_analysis_spec(str(spec_path or _DEFAULT_SPEC))
    cfg = get_config()
    analysis_root = cfg.analysis_output_dir
    inventory = artifact_inventory(analysis_root)
    available_artifacts = {item.name for item in inventory if item.status == "available"}

    def art(name: str) -> str:
        return str(analysis_root / name)

    def has(name: str) -> bool:
        return name in available_artifacts

    def has_all(names: tuple[str, ...]) -> bool:
        return all(has(name) for name in names)

    active_code_artifacts = (
        "active_code_inventory.json",
        "active_python_complexity.json",
        "active_python_import_graph.json",
        "active_rust_workspace_graph.json",
        "active_code_hotspots.json",
        "active_quality_guardrails.json",
        "active_structural_findings.json",
        "active_semantic_static_findings.json",
        "active_rust_dependency_hygiene.json",
        "active_python_dependency_hygiene.json",
        "active_symbol_index.json",
        "active_symbol_changes.json",
        "active_symbol_diffs.json",
        "active_ci_health.json",
    )

    families = {
        "current_state_context": _row(
            "stable" if has_all(("current_state_context_pack.json", "current_state_context_pack.md")) else "missing",
            "Active-project current-state context pack is materialized from the graph-backed evidence spine."
            if has_all(("current_state_context_pack.json", "current_state_context_pack.md"))
            else "Active-project current-state context pack is missing.",
            [art("current_state_context_pack.json"), art("current_state_context_pack.md")],
        ),
        "active_project_snapshot": _row(
            "stable" if has("active_project_snapshot.json") else "missing",
            "Active project tracked-file surface and recent first-parent git facts are materialized."
            if has("active_project_snapshot.json")
            else "Active project snapshot artifact is missing.",
            [art("active_project_snapshot.json")],
        ),
        "active_git_facts": _row(
            "stable" if has_all(("active_commit_facts.json", "active_file_change_facts.json")) else "missing",
            "Active-project default-branch commit and file-change facts are materialized."
            if has_all(("active_commit_facts.json", "active_file_change_facts.json"))
            else "Active commit/file-change fact artifacts are missing.",
            [art("active_commit_facts.json"), art("active_file_change_facts.json")],
        ),
        "active_work_packages": _row(
            "stable" if has("active_work_packages.json") else "missing",
            "Active-project commit-rooted work packages are materialized."
            if has("active_work_packages.json")
            else "Active work-package artifact is missing.",
            [art("active_work_packages.json")],
        ),
        "project_velocity_windows": _row(
            "stable" if has("project_velocity_windows.json") else "missing",
            "Project velocity windows over active facts and cross-source support are materialized."
            if has("project_velocity_windows.json")
            else "Project velocity-window artifact is missing.",
            [art("project_velocity_windows.json")],
        ),
        "active_code_analysis": _row(
            "stable" if has_all(active_code_artifacts) else "missing",
            "Active-project code inventory, language graphs, hotspots, guardrails, dependency hygiene, symbol analysis, and CI health are materialized."
            if has_all(active_code_artifacts)
            else "One or more active code-analysis artifacts are missing.",
            [art(name) for name in active_code_artifacts],
        ),
        "machine_analysis": _machine_analysis_row(analysis_root, available_artifacts),
        "sinex": _row(
            "stable" if has_all(("sinex_structure_metrics.json", "sinex_temporal_metrics.json")) else "missing",
            "Sinex structural and temporal analysis is materialized."
            if has_all(("sinex_structure_metrics.json", "sinex_temporal_metrics.json"))
            else "Sinex analysis artifacts missing.",
            [art("sinex_structure_metrics.json"), art("sinex_temporal_metrics.json")],
        ),
        "polylogue": _row(
            "stable" if has("polylogue_metrics.json") else "missing",
            "Live polylogue repo scan and archive rollup are materialized." if has("polylogue_metrics.json") else "Polylogue metrics artifact missing.",
            [art("polylogue_metrics.json")],
        ),
        "comparison": _row(
            "stable" if has("ecosystem_comparison.json") else "missing",
            "Cross-ecosystem comparison is current." if has("ecosystem_comparison.json") else "Comparison artifact missing.",
            [art("ecosystem_comparison.json")],
        ),
        "work_package_scope": _row(
            "stable" if has("work_package_scope.json") else "missing",
            "Native scope-weighted work-package model is materialized for Sinex and Polylogue."
            if has("work_package_scope.json")
            else "Work-package scope artifact missing.",
            [art("work_package_scope.json")],
        ),
        "dashboard": _row(
            "stable" if has_all(("ecosystem_dashboard.json", "ecosystem_dashboard.html")) else "provisional",
            "Comprehensive dashboard JSON/HTML is available."
            if has_all(("ecosystem_dashboard.json", "ecosystem_dashboard.html"))
            else "Dashboard surface partially materialized.",
            [art("ecosystem_dashboard.json"), art("ecosystem_dashboard.html")],
        ),
        "commit_transport": _row(
            "stable" if has_all(("commit_facts.json", "commit_shards.json")) else "provisional",
            "Commit transport and shard manifests are available."
            if has_all(("commit_facts.json", "commit_shards.json"))
            else "Commit transport artifacts are optional and currently partial or absent.",
            [art("commit_facts.json"), art("commit_shards.json")],
        ),
    }
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_inventory": _inventory_summary(inventory, root=analysis_root),
        "families": families,
    }


def _machine_analysis_row(analysis_root: Path, available_artifacts: set[str]) -> dict[str, object]:
    artifacts = [str(analysis_root / name) for name in MACHINE_ANALYSIS_ARTIFACTS]
    missing = [name for name in MACHINE_ANALYSIS_ARTIFACTS if name not in available_artifacts]
    if missing:
        return _row(
            "missing",
            "One or more machine telemetry, mining, benchmark-infra, support, claim, diagnostic, gap, or readiness artifacts are missing: "
            + ", ".join(missing[:6])
            + ("..." if len(missing) > 6 else ""),
            artifacts,
        )
    readiness = load_json_if_exists(analysis_root / "machine_analysis_readiness.json")
    dimensions = readiness.get("dimensions") if isinstance(readiness, dict) else None
    if not isinstance(dimensions, list) or not dimensions:
        return _row(
            "limited",
            "Machine artifacts are materialized, but readiness dimensions are absent from machine_analysis_readiness.json.",
            artifacts,
        )
    unstable = [
        f"{row.get('dimension') or 'unnamed'}={row.get('status') or 'unknown'}"
        for row in dimensions
        if isinstance(row, dict) and row.get("status") != "stable"
    ]
    if unstable:
        return _row(
            "limited",
            "Machine artifacts are materialized, but readiness gates are not all stable: "
            + ", ".join(unstable[:6])
            + ("..." if len(unstable) > 6 else ""),
            artifacts,
        )
    return _row(
        "stable",
        "Machine telemetry, work-observation mining, validation designs, benchmark-planning infra, support/refusal surfaces, measurement diagnostics, experiment claim packs, gap summaries, and stable readiness coverage are materialized.",
        artifacts,
    )


def run_analysis_status(out_file: str | Path, *, spec_path: str | Path | None = None) -> dict[str, object]:
    payload = build_analysis_status(spec_path=spec_path)
    save_json(out_file, payload, sort_keys=True)
    return payload

def _inventory_summary(inventory: tuple[AnalysisArtifact, ...], *, root: Path) -> dict[str, object]:
    available = tuple(item for item in inventory if item.status == "available")
    partial = tuple(item for item in inventory if item.status != "available")
    return {
        "root": str(root),
        "available_count": len(available),
        "partial_count": len(partial),
        "projects": sorted({project for item in available for project in item.projects}),
    }
