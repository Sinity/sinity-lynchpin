"""Shared evidence graph DTOs without graph-builder dependencies.

DTOs / node+edge types for the evidence graph (the data-model half: node-kind
literals, ``EvidenceNode``, ``EvidenceEdge``, ``EvidenceGraph``). The BUILDER
that populates these from sources is ``lynchpin/graph/evidence_graph.py``.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from .evidence import CostClass, EvidenceCaveat, EvidenceProvenance
EvidenceNodeKind = Literal['commit', 'github_issue', 'github_pr', 'github_ref', 'ai_session', 'ai_work_event', 'raw_log', 'focus_day', 'focus_span', 'deep_work_block', 'circadian_profile', 'focus_loop', 'fragmentation_day', 'attention_day', 'activity_content_day', 'terminal_session', 'terminal_pattern', 'machine_episode', 'machine_context_window', 'machine_below_attribution', 'machine_workload_resource_attribution', 'machine_baseline', 'machine_work_observation', 'machine_work_stage_summary', 'machine_work_test_summary', 'machine_work_failure_summary', 'machine_analysis_feature_frame', 'machine_mining_scan', 'machine_observation_cohort', 'machine_lagged_exposure_summary', 'machine_anomaly_cluster', 'machine_boundary_candidate', 'machine_matched_design', 'machine_matched_comparison', 'machine_cohort_contrast', 'machine_attribution_candidate', 'machine_benchmark_plan', 'machine_benchmark_manifest_group', 'machine_benchmark_run_template', 'machine_benchmark_preflight_run', 'machine_benchmark_execution_queue_item', 'machine_benchmark_run', 'machine_benchmark_phase', 'machine_benchmark_estimate', 'machine_support_assessment', 'machine_experiment_manifest_diagnostics', 'machine_experiment_manifest_diagnostic', 'machine_experiment_claim', 'machine_mechanism_hypothesis', 'machine_negative_control', 'machine_calibration_fixture', 'machine_measurement_check', 'machine_instrumentation_gap', 'machine_assumption_check', 'listening_session', 'web_domain_day', 'sleep_quality', 'health_metric', 'temporal_changepoint', 'temporal_trend', 'temporal_anomaly', 'temporal_rhythm', 'readiness_forecast', 'bookmark_activity', 'communication_activity', 'arbtt_focus_activity', 'analysis_artifact', 'analysis_claim', 'clipboard_entry', 'irc_conversation']
EvidenceRelation = Literal['references', 'same_project_day', 'temporal_overlap', 'temporal_proximity', 'mentions_project', 'file_overlap', 'tool_overlap', 'symbol_overlap', 'overlaps_machine_pressure', 'below_supports_episode', 'workload_resource_supports_episode', 'baseline_deviation', 'scan_uses_feature_frame', 'candidate_from_mining_scan', 'candidate_from_artifact', 'candidate_uses_feature_frame', 'candidate_from_cohort', 'candidate_from_boundary', 'candidate_validated_by_split', 'contrast_estimates_cohort', 'comparison_matches_cohorts', 'benchmark_plan_for_candidate', 'plan_investigates_candidate', 'manifest_group_from_plan', 'run_template_in_manifest_group', 'preflight_checks_run_template', 'execution_queue_prioritizes_manifest_group', 'execution_queue_for_candidate', 'support_assessment_for_candidate', 'mechanism_explains_candidate', 'mechanism_summarizes_assessment', 'negative_control_checks_candidate', 'instrumentation_gap_blocks_mechanism', 'instrumentation_gap_blocks_assessment', 'instrumentation_gap_blocks_candidate', 'assumption_check_limits_claim', 'refusal_resolves_candidate', 'claim_resolves_candidate', 'run_in_plan', 'phase_in_run', 'estimate_summarizes_runs', 'run_overlaps_machine_episode', 'run_overlaps_telemetry_window', 'experiment_claim_support']

@dataclass(frozen=True)
class EvidenceNode:
    id: str
    kind: EvidenceNodeKind
    source: str
    date: date
    project: str | None
    summary: str
    start: datetime | None = None
    end: datetime | None = None
    url: str | None = None
    payload: dict[str, Any] | None = None
    provenance: EvidenceProvenance | None = None
    caveats: tuple[EvidenceCaveat, ...] = ()

@dataclass(frozen=True)
class EvidenceEdge:
    source_id: str
    target_id: str
    relation: EvidenceRelation
    evidence: str
    weight: float = 1.0

@dataclass(frozen=True)
class EvidenceTimelineEntry:
    node_id: str
    date: date
    when: datetime | None
    project: str | None
    source: str
    kind: EvidenceNodeKind
    summary: str

@dataclass(frozen=True)
class EvidenceRelationEntry:
    source_node_id: str
    target_node_id: str
    source_source: str
    target_source: str
    relation: EvidenceRelation
    evidence: str
    weight: float
    date: date
    project: str | None
    source_summary: str
    target_summary: str

@dataclass(frozen=True)
class EvidenceGraph:
    start: date
    end: date
    generated_at: datetime
    mode: CostClass = 'materialized'
    nodes: tuple[EvidenceNode, ...] = ()
    edges: tuple[EvidenceEdge, ...] = ()
    caveats: tuple[EvidenceCaveat, ...] = ()

    def nodes_by_project_day(self) -> dict[tuple[date, str], tuple[EvidenceNode, ...]]:
        grouped: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
        for node in self.nodes:
            if node.project:
                grouped[node.date, node.project].append(node)
        return {key: tuple(value) for key, value in grouped.items()}

    def node_map(self) -> dict[str, EvidenceNode]:
        return {node.id: node for node in self.nodes}
__all__ = ['EvidenceEdge', 'EvidenceGraph', 'EvidenceNode', 'EvidenceNodeKind', 'EvidenceRelation', 'EvidenceRelationEntry', 'EvidenceTimelineEntry']
