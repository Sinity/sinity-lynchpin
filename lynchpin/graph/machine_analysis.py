"""Machine-analysis evidence graph nodes."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_materialized_analysis_artifact
from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceEdge, EvidenceNode
from ..core.parse import parse_datetime
from ..core.primitives import logical_date
from ..core.projects import canonical_project_name


def add_machine_analysis_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    *,
    start: date,
    end: date,
    selected: set[str],
    exclude_names: frozenset[str],
) -> None:
    artifacts = {
        "episodes": "machine_episode_analysis.json",
        "context": "machine_context_windows.json",
        "work_observations": "machine_work_observations.json",
        "feature_frames": "machine_analysis_feature_frames.json",
        "mining": "machine_mining.json",
        "validation_design": "machine_validation_design.json",
        "matched_designs": "machine_matched_designs.json",
        "negative_controls": "machine_negative_controls.json",
        "comparisons": "machine_comparisons.json",
        "benchmark_plans": "machine_benchmark_plans.json",
        "benchmark_manifest_bundle": "machine_benchmark_manifest_bundle.json",
        "benchmark_preflight": "machine_benchmark_preflight.json",
        "benchmark_execution_handoff": "machine_benchmark_execution_handoff.json",
        "manifest_diagnostics": "machine_experiment_manifest_diagnostics.json",
        "support_assessment": "machine_support_assessment.json",
        "mechanisms": "machine_mechanism_hypotheses.json",
        "instrumentation_gaps": "machine_instrumentation_gaps.json",
        "calibration": "machine_calibration_fixtures.json",
        "measurement_system": "machine_measurement_system.json",
        "candidates": "machine_attribution_candidates.json",
        "attribution_claims": "machine_attribution_claims.json",
        "assumption_checks": "machine_assumption_checks.json",
        "below": "machine_below_attribution.json",
        "below_export_handoff": "machine_below_export_handoff.json",
        "baselines": "machine_observational_baselines.json",
        "claims": "machine_experiment_claims.json",
    }
    payloads = {
        key: _load_machine_graph_artifact(name)
        for key, name in artifacts.items()
        if name not in exclude_names
    }

    episode_ids: dict[tuple[str, str, str, str], str] = {}
    episode_bound_ids: dict[tuple[str, str, str, str], str] = {}
    selected_episode_keys = _selected_machine_episode_keys(
        context_payload=payloads.get("context"),
        claims_payload=payloads.get("claims"),
        selected=selected,
    )
    episodes = _machine_rows(payloads.get("episodes"), "episodes")
    for row in episodes:
        episode_key = _machine_episode_key(row)
        if selected and episode_key not in selected_episode_keys:
            continue
        started_at = _machine_dt(row.get("started_at"))
        ended_at = _machine_dt(row.get("ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        node_id = _machine_episode_id(row)
        episode_ids[episode_key] = node_id
        episode_bound_ids[_machine_episode_bounds_key(row)] = node_id
        kind = str(row.get("kind") or "unknown")
        subject = str(row.get("subject") or "")
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_episode",
                source="machine",
                date=logical_date(started_at),
                project=None,
                start=started_at,
                end=ended_at,
                summary=f"{kind}: {subject}".rstrip(": "),
                payload={
                    "kind": kind,
                    "host": row.get("host"),
                    "subject": row.get("subject"),
                    "severity": row.get("severity"),
                    "confidence": row.get("confidence"),
                    "sample_count": row.get("sample_count"),
                    "sources": row.get("sources") or (),
                    "evidence": row.get("evidence") or (),
                    "payload": row.get("payload") or {},
                },
                provenance=EvidenceProvenance("machine", "materialized", path=artifacts["episodes"]),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )

    for row in _machine_rows(payloads.get("context"), "windows"):
        started_at = _machine_dt(row.get("started_at"))
        ended_at = _machine_dt(row.get("ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        projects = tuple(
            project
            for project in (canonical_project_name(str(value)) for value in row.get("projects", ()) if value)
            if project is not None
        )
        if selected and not set(projects).intersection(selected):
            continue
        project = projects[0] if len(projects) == 1 else None
        node_id = f"machine-context:{row.get('window_id') or started_at.isoformat()}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_context_window",
                source="machine",
                date=logical_date(started_at),
                project=project,
                start=started_at,
                end=ended_at,
                summary=str(row.get("summary") or row.get("interpretation") or "machine/work context window"),
                payload={
                    "window_id": row.get("window_id"),
                    "projects": projects,
                    "source": row.get("source"),
                    "work_kind": row.get("work_kind"),
                    "duration_seconds": row.get("duration_seconds"),
                    "episode_count": row.get("episode_count"),
                    "overlap_seconds": row.get("overlap_seconds"),
                    "interpretation": row.get("interpretation"),
                },
                provenance=EvidenceProvenance("machine", "materialized", path=artifacts["context"]),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        for embedded in _machine_embedded_rows(row, "episodes"):
            target_id = episode_ids.get(_machine_episode_key(embedded)) or _machine_episode_id(embedded)
            edges.append(
                EvidenceEdge(
                    node_id,
                    target_id,
                    "overlaps_machine_pressure",
                    f"work window overlaps {embedded.get('kind')} for {embedded.get('overlap_seconds')}s",
                    _bounded_weight(embedded.get("overlap_seconds"), row.get("duration_seconds")),
                )
            )

    for row in _machine_rows(payloads.get("below"), "attributions"):
        started_at = _machine_dt(row.get("episode_started_at"))
        ended_at = _machine_dt(row.get("episode_ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        node_id = f"machine-below:{row.get('capture_id')}:{row.get('episode_kind')}:{started_at.isoformat()}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_below_attribution",
                source="below",
                date=logical_date(started_at),
                project=None,
                start=started_at,
                end=ended_at,
                summary=f"below attribution for {row.get('episode_kind')} in {row.get('capture_id')}",
                payload=row,
                provenance=EvidenceProvenance("below", "materialized", path=artifacts["below"]),
                caveats=tuple(EvidenceCaveat("below", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        attributed_episode_id = episode_ids.get(_machine_attribution_episode_key(row)) or episode_bound_ids.get(_machine_attribution_bounds_key(row))
        if attributed_episode_id is not None:
            edges.append(
                EvidenceEdge(
                    node_id,
                    attributed_episode_id,
                    "below_supports_episode",
                    f"bounded below capture overlaps {row.get('episode_kind')}",
                    _bounded_weight(row.get("overlap_seconds"), None),
                )
            )

    for row in _machine_rows(payloads.get("below"), "workload_resource_attributions"):
        started_at = _machine_dt(row.get("episode_started_at"))
        ended_at = _machine_dt(row.get("episode_ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        node_id = (
            f"machine-workload-resource:{row.get('work_source')}:{row.get('work_source_id')}:"
            f"{row.get('episode_kind')}:{started_at.isoformat()}"
        )
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_workload_resource_attribution",
                source="machine",
                date=logical_date(started_at),
                project=project,
                start=started_at,
                end=ended_at,
                summary=(
                    f"workload resource attribution for {row.get('episode_kind')}"
                    f" from {row.get('work_source_id')}"
                ),
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifacts["below"]),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        attributed_episode_id = episode_bound_ids.get(_machine_attribution_bounds_key(row))
        if attributed_episode_id is not None:
            edges.append(
                EvidenceEdge(
                    node_id,
                    attributed_episode_id,
                    "workload_resource_supports_episode",
                    f"measured workload resources overlap {row.get('episode_kind')}",
                    _bounded_weight(row.get("overlap_seconds"), None),
                )
            )

    _add_machine_baseline_nodes(nodes, payloads.get("baselines"), start=start, end=end, selected=selected, artifact_name=artifacts["baselines"])
    _add_machine_work_observation_nodes(
        nodes,
        payloads.get("work_observations"),
        start=start,
        end=end,
        selected=selected,
        artifact_name=artifacts["work_observations"],
    )
    _add_machine_mining_nodes(
        nodes,
        edges,
        feature_payload=payloads.get("feature_frames"),
        mining_payload=payloads.get("mining"),
        validation_payload=payloads.get("validation_design"),
        matched_payload=payloads.get("matched_designs"),
        comparisons_payload=payloads.get("comparisons"),
        start=start,
        end=end,
        selected=selected,
        feature_artifact=artifacts["feature_frames"],
        mining_artifact=artifacts["mining"],
        validation_artifact=artifacts["validation_design"],
        matched_artifact=artifacts["matched_designs"],
        comparisons_artifact=artifacts["comparisons"],
    )
    _add_machine_candidate_nodes(nodes, edges, payloads.get("candidates"), start=start, selected=selected, artifact_name=artifacts["candidates"])
    _add_machine_claim_nodes(nodes, edges, payloads.get("claims"), start=start, end=end, selected=selected, episode_ids=episode_ids, artifact_name=artifacts["claims"])
    _add_machine_negative_control_nodes(nodes, edges, payloads.get("negative_controls"), start=start, selected=selected, artifact_name=artifacts["negative_controls"])
    _add_machine_benchmark_plan_nodes(nodes, edges, payloads.get("benchmark_plans"), start=start, selected=selected, artifact_name=artifacts["benchmark_plans"])
    _add_machine_benchmark_manifest_bundle_nodes(
        nodes,
        edges,
        payloads.get("benchmark_manifest_bundle"),
        start=start,
        artifact_name=artifacts["benchmark_manifest_bundle"],
    )
    _add_machine_benchmark_preflight_nodes(
        nodes,
        edges,
        payloads.get("benchmark_preflight"),
        start=start,
        artifact_name=artifacts["benchmark_preflight"],
    )
    _add_machine_benchmark_execution_handoff_nodes(
        nodes,
        edges,
        payloads.get("benchmark_execution_handoff"),
        start=start,
        artifact_name=artifacts["benchmark_execution_handoff"],
    )
    _add_machine_below_export_handoff_nodes(
        nodes,
        edges,
        payloads.get("below_export_handoff"),
        start=start,
        end=end,
        episode_ids=episode_ids,
        episode_bound_ids=episode_bound_ids,
        artifact_name=artifacts["below_export_handoff"],
    )
    _add_machine_manifest_diagnostic_nodes(nodes, payloads.get("manifest_diagnostics"), start=start, artifact_name=artifacts["manifest_diagnostics"])
    _add_machine_support_assessment_nodes(nodes, edges, payloads.get("support_assessment"), start=start, selected=selected, artifact_name=artifacts["support_assessment"])
    _add_machine_mechanism_nodes(nodes, edges, payloads.get("mechanisms"), start=start, selected=selected, artifact_name=artifacts["mechanisms"])
    _add_machine_instrumentation_gap_nodes(nodes, edges, payloads.get("instrumentation_gaps"), start=start, selected=selected, artifact_name=artifacts["instrumentation_gaps"])
    _add_machine_calibration_nodes(nodes, payloads.get("calibration"), start=start, artifact_name=artifacts["calibration"])
    _add_machine_measurement_nodes(nodes, payloads.get("measurement_system"), start=start, artifact_name=artifacts["measurement_system"])
    _add_machine_attribution_claim_nodes(nodes, edges, payloads.get("attribution_claims"), start=start, selected=selected, artifact_name=artifacts["attribution_claims"])
    _add_machine_assumption_check_nodes(nodes, edges, payloads.get("assumption_checks"), start=start, selected=selected, artifact_name=artifacts["assumption_checks"])


def _add_machine_baseline_nodes(
    nodes: list[EvidenceNode],
    payload: object,
    *,
    start: date,
    end: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    if not _machine_payload_overlaps(payload, start=start, end=end):
        return
    for section in ("by_hardware_regime", "work_context", "era_comparisons"):
        for idx, row in enumerate(_machine_rows(payload, section)):
            first = _machine_dt(row.get("first_observed_at") or row.get("boundary")) or datetime.combine(start, datetime.min.time()).astimezone()
            last = _machine_dt(row.get("last_observed_at")) or datetime.combine(end, datetime.max.time()).astimezone()
            if not _machine_overlaps(first, last, start=start, end=end):
                continue
            key = str(row.get("key") or row.get("boundary") or idx)
            project = canonical_project_name(key) if row.get("dimension") == "project" else None
            if project == "(unattributed)":
                project = None
            if selected and project is not None and project not in selected:
                continue
            nodes.append(
                EvidenceNode(
                    id=f"machine-baseline:{section}:{key}",
                    kind="machine_baseline",
                    source="machine",
                    date=logical_date(first),
                    project=project,
                    start=first,
                    end=last,
                    summary=f"{section} baseline: {key}",
                    payload={"section": section, **row},
                    provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                    caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
                )
            )


def _add_machine_work_observation_nodes(
    nodes: list[EvidenceNode],
    payload: object,
    *,
    start: date,
    end: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "daily"):
        observed_date = _date_value(row.get("date"))
        if observed_date is None or not start <= observed_date <= end:
            continue
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        command = row.get("command") if isinstance(row.get("command"), list) else []
        command_key = " ".join(str(part) for part in command) or "unknown"
        count = row.get("observation_count") or 0
        failed = row.get("failed_count") or 0
        node_id = f"machine-work-observation:{observed_date.isoformat()}:{project or 'none'}:{command_key}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_work_observation",
                source="machine",
                date=observed_date,
                project=project,
                summary=f"{count} work observations for {command_key} ({failed} failed)",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", "xtask history is observational, not controlled benchmark evidence"),
                ),
            )
        )

    for row in _machine_rows(payload, "stage_summaries"):
        stage = str(row.get("stage_name") or "unknown")
        node_id = f"machine-work-stage-summary:{stage}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_work_stage_summary",
                source="machine",
                date=start,
                project=None,
                summary=(
                    f"{stage} stage timing: n={row.get('observation_count')}, "
                    f"p95={row.get('p95_duration_s')}, max={row.get('max_duration_s')}"
                ),
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", "stage summary is observational and not a fixed-workload benchmark"),
                ),
            )
        )

    for row in _machine_rows(payload, "test_summaries"):
        package = str(row.get("package") or "unknown")
        status = str(row.get("status") or "unknown")
        node_id = f"machine-work-test-summary:{package}:{status}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_work_test_summary",
                source="machine",
                date=start,
                project=None,
                summary=(
                    f"{package}/{status} test timing: n={row.get('test_count')}, "
                    f"p95={row.get('p95_duration_s')}, max={row.get('max_duration_s')}"
                ),
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", "test summary is observational and grouped by package/status"),
                ),
            )
        )

    for row in _machine_rows(payload, "failure_summaries"):
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        signature = _work_failure_signature(row)
        nodes.append(
            EvidenceNode(
                id=f"machine-work-failure-summary:{signature}",
                kind="machine_work_failure_summary",
                source="machine",
                date=start,
                project=project,
                summary=(
                    f"{row.get('failure_kind') or 'unknown'} failures at "
                    f"{row.get('package') or row.get('stage_name') or project or 'unknown'}: "
                    f"{row.get('failure_count')} failures"
                ),
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", "failure concentration is observational and command-mix sensitive"),
                ),
            )
        )


def _add_machine_mining_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    *,
    feature_payload: object,
    mining_payload: object,
    validation_payload: object,
    matched_payload: object,
    comparisons_payload: object,
    start: date,
    end: date,
    selected: set[str],
    feature_artifact: str,
    mining_artifact: str,
    validation_artifact: str,
    matched_artifact: str,
    comparisons_artifact: str,
) -> None:
    if not (
        _machine_payload_overlaps(feature_payload, start=start, end=end)
        or _machine_payload_overlaps(mining_payload, start=start, end=end)
    ):
        return
    cohort_rows = []
    for row in _machine_rows(mining_payload, "cohorts"):
        project = canonical_project_name(str(row.get("dimensions", {}).get("project"))) if isinstance(row.get("dimensions"), dict) and row.get("dimensions", {}).get("project") else None
        if selected and project not in selected:
            continue
        cohort_rows.append((row, project))
    if selected and not cohort_rows:
        return

    frame = feature_payload.get("frame") if isinstance(feature_payload, dict) and isinstance(feature_payload.get("frame"), dict) else {}
    frame_id = str(frame.get("frame_id") or "")
    frame_node_id: str | None = None
    if frame_id:
        frame_node_id = f"machine-feature-frame:{frame_id}"
        nodes.append(
            EvidenceNode(
                id=frame_node_id,
                kind="machine_analysis_feature_frame",
                source="machine",
                date=start,
                project=None,
                summary=f"machine feature frame: {frame.get('row_count', 0)} {frame.get('unit_type', 'rows')}",
                payload={
                    "frame_id": frame_id,
                    "unit_type": frame.get("unit_type"),
                    "row_count": frame.get("row_count"),
                    "outcome_metric": frame.get("outcome_metric"),
                    "leakage_status": frame.get("leakage_status"),
                    "missing_value_count": frame.get("missing_value_count"),
                    "censored_count": frame.get("censored_count"),
                },
                provenance=EvidenceProvenance("machine", "materialized", path=feature_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in frame.get("caveats", ()) if c),
            )
        )

    scan = mining_payload.get("scan") if isinstance(mining_payload, dict) and isinstance(mining_payload.get("scan"), dict) else {}
    scan_id = str(scan.get("scan_id") or "")
    scan_node_id: str | None = None
    if scan_id:
        scan_node_id = f"machine-mining-scan:{scan_id}"
        nodes.append(
            EvidenceNode(
                id=scan_node_id,
                kind="machine_mining_scan",
                source="machine",
                date=start,
                project=None,
                summary=f"machine mining scan: {scan.get('comparison_universe_size', 0)} cohorts searched",
                payload=scan,
                provenance=EvidenceProvenance("machine", "materialized", path=mining_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in scan.get("caveats", ()) if c),
            )
        )
        if frame_node_id is not None:
            edges.append(EvidenceEdge(scan_node_id, frame_node_id, "scan_uses_feature_frame", "mining scan uses leakage-checked feature frame", 1.0))

    for row, project in cohort_rows:
        node_id = f"machine-observation-cohort:{row.get('cohort_id')}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_observation_cohort",
                source="machine",
                date=start,
                project=project,
                summary=f"machine cohort {row.get('dimensions')} n={row.get('row_count')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=mining_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        if scan_node_id is not None:
            edges.append(EvidenceEdge(node_id, scan_node_id, "candidate_from_mining_scan", "cohort came from recorded mining scan universe", 1.0))

    for row in _machine_rows(mining_payload, "lagged_exposures"):
        project = canonical_project_name(str(row.get("dimensions", {}).get("project"))) if isinstance(row.get("dimensions"), dict) and row.get("dimensions", {}).get("project") else None
        if selected and project not in selected:
            continue
        summary_id = str(row.get("summary_id") or "")
        if not summary_id:
            continue
        node_id = summary_id if summary_id.startswith("machine-lagged-exposure:") else f"machine-lagged-exposure:{summary_id}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_lagged_exposure_summary",
                source="machine",
                date=start,
                project=project,
                summary=f"lagged {row.get('pressure_metric')}: delta={row.get('median_delta')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=mining_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        if scan_node_id is not None:
            edges.append(EvidenceEdge(node_id, scan_node_id, "candidate_from_mining_scan", "lagged exposure came from recorded mining scan universe", 1.0))

    for row in _machine_rows(mining_payload, "anomaly_clusters"):
        project = canonical_project_name(str(row.get("dimensions", {}).get("project"))) if isinstance(row.get("dimensions"), dict) and row.get("dimensions", {}).get("project") else None
        if selected and project not in selected:
            continue
        cluster_id = str(row.get("cluster_id") or "")
        if not cluster_id:
            continue
        node_id = cluster_id if cluster_id.startswith("machine-anomaly-cluster:") else f"machine-anomaly-cluster:{cluster_id}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_anomaly_cluster",
                source="machine",
                date=start,
                project=project,
                summary=f"machine anomaly cluster n={row.get('anomaly_count')} max={row.get('max_outcome')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=mining_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        if scan_node_id is not None:
            edges.append(EvidenceEdge(node_id, scan_node_id, "candidate_from_mining_scan", "anomaly cluster came from recorded mining scan universe", 1.0))

    cohort_node_ids = {
        str(row.get("cohort_id")): f"machine-observation-cohort:{row.get('cohort_id')}"
        for row, _project in cohort_rows
        if row.get("cohort_id")
    }
    for row in _machine_rows(comparisons_payload, "contrasts"):
        project = canonical_project_name(str(row.get("dimensions", {}).get("project"))) if isinstance(row.get("dimensions"), dict) and row.get("dimensions", {}).get("project") else None
        if selected and project not in selected:
            continue
        node_id = f"machine-cohort-contrast:{row.get('contrast_id')}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_cohort_contrast",
                source="machine",
                date=start,
                project=project,
                summary=f"machine contrast {row.get('dimensions')} delta={row.get('median_delta')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=comparisons_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        cohort_node_id = cohort_node_ids.get(str(row.get("cohort_id")))
        if cohort_node_id is not None:
            edges.append(EvidenceEdge(node_id, cohort_node_id, "contrast_estimates_cohort", "contrast estimates cohort against rest of frame", 1.0))

    for row in _machine_rows(validation_payload, "boundaries"):
        project = canonical_project_name(str(row.get("dimensions", {}).get("project"))) if isinstance(row.get("dimensions"), dict) and row.get("dimensions", {}).get("project") else None
        if selected and project not in selected:
            continue
        boundary_at = _machine_dt(row.get("boundary_at"))
        if boundary_at is None or not start <= boundary_at.date() <= end:
            continue
        nodes.append(
            EvidenceNode(
                id=f"machine-boundary:{row.get('boundary_id')}",
                kind="machine_boundary_candidate",
                source="machine",
                date=logical_date(boundary_at),
                project=project,
                start=boundary_at,
                summary=f"machine boundary {row.get('boundary_type')}: {row.get('dimensions')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=validation_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )

    for row in _machine_rows(matched_payload, "designs"):
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        boundary_at = _machine_dt(row.get("boundary_at"))
        if boundary_at is None or not start <= boundary_at.date() <= end:
            continue
        caveats = [str(c) for c in row.get("caveats", ()) if c]
        nodes.append(
            EvidenceNode(
                id=f"machine-matched-design:{row.get('design_id')}",
                kind="machine_matched_comparison",
                source="machine",
                date=logical_date(boundary_at),
                project=project,
                start=boundary_at,
                summary=(
                    f"machine matched design {row.get('control_family')}: "
                    f"did={row.get('difference_in_differences')} status={row.get('identification_status')}"
                ),
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=matched_artifact),
                caveats=tuple(EvidenceCaveat("machine", "partial", c) for c in caveats),
            )
        )


def _add_machine_claim_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    end: date,
    selected: set[str],
    episode_ids: dict[tuple[str, str, str, str], str],
    artifact_name: str,
) -> None:
    run_ids_by_group: dict[str, list[str]] = {}
    for row in _machine_rows(payload, "claim_packs"):
        started_at = _machine_dt(row.get("started_at"))
        ended_at = _machine_dt(row.get("ended_at")) or started_at
        if started_at is None or not _machine_overlaps(started_at, ended_at, start=start, end=end):
            continue
        project = _project_from_path(row.get("git_root") or row.get("cwd"))
        if selected and project is not None and project not in selected:
            continue
        run_id = str(row.get("run_id") or started_at.isoformat())
        run_group_id = str(row.get("run_group_id")) if row.get("run_group_id") else None
        run_node_id = f"machine-benchmark-run:{run_id}"
        if run_group_id:
            run_ids_by_group.setdefault(run_group_id, []).append(run_node_id)
        manifest_validation = row.get("manifest_validation") if isinstance(row.get("manifest_validation"), dict) else {}
        nodes.append(
            EvidenceNode(
                id=run_node_id,
                kind="machine_benchmark_run",
                source="machine",
                date=logical_date(started_at),
                project=project,
                start=started_at,
                end=ended_at,
                summary=f"{row.get('claim_mode')}: {row.get('workload')}",
                payload={
                    "run_id": row.get("run_id"),
                    "run_group_id": row.get("run_group_id"),
                    "claim_mode": row.get("claim_mode"),
                    "workload": row.get("workload"),
                    "treatment_label": row.get("treatment_label"),
                    "cache_condition": row.get("cache_condition"),
                    "derivation_key": row.get("derivation_key"),
                    "duration_seconds": row.get("duration_seconds"),
                    "exit_status": row.get("exit_status"),
                    "execution_outcome": row.get("execution_outcome") or {},
                    "manifest_validation_status": _machine_manifest_validation_status(manifest_validation),
                    "manifest_validation_issues": tuple(str(item) for item in manifest_validation.get("issues", ()) if item),
                    "manifest_validation_warnings": tuple(str(item) for item in manifest_validation.get("warnings", ()) if item),
                    "telemetry": row.get("telemetry") or {},
                    "internal_json_path": row.get("nix_internal_json_path"),
                },
                provenance=EvidenceProvenance("machine", "materialized", path=str(row.get("manifest_path") or artifact_name)),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        node_id = f"machine-claim:{run_id}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_experiment_claim",
                source="machine",
                date=logical_date(started_at),
                project=project,
                start=started_at,
                end=ended_at,
                summary=f"{row.get('claim_mode')}: {row.get('workload')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=str(row.get("manifest_path") or artifact_name)),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        for embedded in _machine_embedded_rows(row, "episodes"):
            target_id = episode_ids.get(_machine_episode_key(embedded)) or _machine_episode_id(embedded)
            edges.append(
                EvidenceEdge(
                    run_node_id,
                    target_id,
                    "run_overlaps_machine_episode",
                    f"benchmark run overlaps {embedded.get('kind')}",
                    _bounded_weight(embedded.get("overlap_seconds"), row.get("duration_seconds")),
                )
            )
            edges.append(
                EvidenceEdge(
                    node_id,
                    target_id,
                    "experiment_claim_support",
                    f"experiment claim includes {embedded.get('kind')} overlap",
                    _bounded_weight(embedded.get("overlap_seconds"), row.get("duration_seconds")),
                )
            )
        internal_json = row.get("internal_json") if isinstance(row.get("internal_json"), dict) else {}
        for idx, phase in enumerate(_machine_rows(internal_json, "phases")):
            phase_id = str(phase.get("activity_id") or phase.get("id") or idx)
            phase_node_id = f"machine-benchmark-phase:{run_id}:{phase_id}"
            nodes.append(
                EvidenceNode(
                    id=phase_node_id,
                    kind="machine_benchmark_phase",
                    source="machine",
                    date=logical_date(started_at),
                    project=project,
                    start=_machine_dt(phase.get("started_at")) or started_at,
                    end=_machine_dt(phase.get("ended_at")),
                    summary=f"{phase.get('status') or 'phase'}: {phase.get('name') or phase.get('activity_type') or phase_id}",
                    payload={"run_id": row.get("run_id"), "run_group_id": row.get("run_group_id"), **phase},
                    provenance=EvidenceProvenance("machine", "materialized", path=str(row.get("nix_internal_json_path") or artifact_name)),
                    caveats=(),
                )
            )
            edges.append(EvidenceEdge(phase_node_id, run_node_id, "phase_in_run", "Nix internal-json phase belongs to benchmark run", 1.0))
    for row in _machine_rows(payload, "effect_estimates"):
        run_group_id = str(row.get("run_group_id") or "")
        if not run_group_id:
            continue
        metric = str(row.get("metric") or "unknown_metric")
        estimator = str(row.get("estimator") or "unknown_estimator")
        estimate_node_id = f"machine-benchmark-estimate:{run_group_id}:{metric}:{estimator}"
        nodes.append(
            EvidenceNode(
                id=estimate_node_id,
                kind="machine_benchmark_estimate",
                source="machine",
                date=start,
                project=None,
                summary=f"{estimator}: {metric} delta={row.get('delta')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(),
            )
        )
        for run_node_id in run_ids_by_group.get(run_group_id, ()):
            edges.append(EvidenceEdge(estimate_node_id, run_node_id, "estimate_summarizes_runs", "benchmark estimate summarizes manifest-backed run", 1.0))


def _add_machine_candidate_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "candidates"):
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id:
            continue
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        source_artifacts = tuple(str(value) for value in row.get("source_artifacts", ()) if value)
        source_ids = tuple(str(value) for value in row.get("source_ids", ()) if value)
        nodes.append(
            EvidenceNode(
                id=candidate_id,
                kind="machine_attribution_candidate",
                source="machine",
                date=start,
                project=project,
                summary=str(row.get("summary") or "machine attribution candidate"),
                payload={
                    "candidate_id": candidate_id,
                    "metric": row.get("metric"),
                    "suspected_factor": row.get("suspected_factor"),
                    "mechanism_family": row.get("mechanism_family"),
                    "support_ceiling": row.get("support_ceiling"),
                    "priority_score": row.get("priority_score"),
                    "score_components": row.get("score_components") or {},
                    "source_artifacts": source_artifacts,
                    "source_ids": source_ids,
                    "discovery_window": row.get("discovery_window"),
                    "validation_status": row.get("validation_status"),
                    "mining_scan_id": row.get("mining_scan_id"),
                    "rank_within_scan": row.get("rank_within_scan"),
                    "pareto_frontier": row.get("pareto_frontier"),
                    "suggested_benchmark_manifest": row.get("suggested_benchmark_manifest") or {},
                },
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        for source_id in source_ids:
            for target_id in _candidate_source_targets(source_id):
                edges.append(
                    EvidenceEdge(
                        candidate_id,
                        target_id,
                        "candidate_from_artifact",
                        "candidate was generated from a mined machine artifact row",
                        1.0,
                    )
                )


def _add_machine_benchmark_plan_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "plans"):
        candidate = row.get("manifest_preview", {}).get("candidate") if isinstance(row.get("manifest_preview"), dict) else {}
        if not isinstance(candidate, dict):
            candidate = {}
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project is not None and project not in selected:
            continue
        node_id = f"machine-benchmark-plan:{row.get('plan_id')}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_benchmark_plan",
                source="machine",
                date=start,
                project=project,
                summary=f"benchmark plan {row.get('planning_status')}: {row.get('primary_metric')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        candidate_id = candidate.get("candidate_id")
        if candidate_id:
            edges.append(EvidenceEdge(node_id, str(candidate_id), "plan_investigates_candidate", "dry-run plan was generated from candidate", 1.0))


def _add_machine_benchmark_manifest_bundle_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    artifact_name: str,
) -> None:
    for group in _machine_rows(payload, "groups"):
        run_group_id = str(group.get("run_group_id") or "")
        if not run_group_id:
            continue
        group_id = f"machine-benchmark-manifest-group:{run_group_id}"
        nodes.append(
            EvidenceNode(
                id=group_id,
                kind="machine_benchmark_manifest_group",
                source="machine",
                date=start,
                project=None,
                summary=f"benchmark manifest group {run_group_id}: {group.get('run_count', 0)} run templates",
                payload={
                    "run_group_id": run_group_id,
                    "plan_id": group.get("plan_id"),
                    "candidate_id": group.get("candidate_id"),
                    "planning_status": group.get("planning_status"),
                    "support_ceiling": group.get("support_ceiling"),
                    "primary_metric": group.get("primary_metric"),
                    "run_count": group.get("run_count"),
                    "pre_analysis": group.get("pre_analysis") or {},
                    "caveats": group.get("caveats") or [],
                },
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in group.get("caveats", ()) if c),
            )
        )
        plan_id = str(group.get("plan_id") or "")
        if plan_id:
            edges.append(
                EvidenceEdge(
                    group_id,
                    f"machine-benchmark-plan:{plan_id}",
                    "manifest_group_from_plan",
                    "exportable manifest group was generated from benchmark plan",
                    1.0,
                )
            )
        for run in _dict_rows(group.get("run_templates")):
            run_id = str(run.get("run_id") or "")
            if not run_id:
                continue
            run_node_id = f"machine-benchmark-run-template:{run_group_id}:{run_id}"
            nodes.append(
                EvidenceNode(
                    id=run_node_id,
                    kind="machine_benchmark_run_template",
                    source="machine",
                    date=start,
                    project=None,
                    summary=(
                        f"benchmark run template {run_id}: {run.get('treatment_label')} "
                        f"{run.get('cache_condition')}"
                    ),
                    payload={
                        "run_group_id": run_group_id,
                        "run_id": run_id,
                        "sequence_index": run.get("sequence_index"),
                        "treatment_label": run.get("treatment_label"),
                        "cache_condition": run.get("cache_condition"),
                        "derivation_key": run.get("derivation_key"),
                        "telemetry_window_id": run.get("telemetry_window_id"),
                    },
                    provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                    caveats=(
                        EvidenceCaveat("machine", "partial", "run template is not executed benchmark evidence"),
                    ),
                )
            )
            edges.append(
                EvidenceEdge(
                    run_node_id,
                    group_id,
                    "run_template_in_manifest_group",
                    "run template belongs to exportable manifest group",
                    1.0,
                )
            )


def _add_machine_benchmark_preflight_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    artifact_name: str,
) -> None:
    for group in _machine_rows(payload, "groups"):
        run_group_id = str(group.get("run_group_id") or "")
        if not run_group_id:
            continue
        for run in _dict_rows(group.get("runs")):
            run_id = str(run.get("run_id") or "")
            if not run_id:
                continue
            node_id = f"machine-benchmark-preflight-run:{run_group_id}:{run_id}"
            template_id = f"machine-benchmark-run-template:{run_group_id}:{run_id}"
            nodes.append(
                EvidenceNode(
                    id=node_id,
                    kind="machine_benchmark_preflight_run",
                    source="machine",
                    date=start,
                    project=None,
                    summary=(
                        f"benchmark preflight {run_id}: "
                        f"{'ready' if run.get('ready_to_export') else 'blocked'}"
                    ),
                    payload=run,
                    provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                    caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in run.get("warnings", ()) if c),
                )
            )
            edges.append(
                EvidenceEdge(
                    node_id,
                    template_id,
                    "preflight_checks_run_template",
                    "preflight validates exportable run template before execution",
                    1.0,
                )
            )


def _add_machine_benchmark_execution_handoff_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    artifact_name: str,
) -> None:
    for item in _machine_rows(payload, "items"):
        handoff_id = str(item.get("handoff_id") or "")
        run_group_id = str(item.get("run_group_id") or "")
        candidate_id = str(item.get("candidate_id") or "")
        if not handoff_id or not run_group_id:
            continue
        nodes.append(
            EvidenceNode(
                id=handoff_id,
                kind="machine_benchmark_execution_handoff_item",
                source="machine",
                date=start,
                project=canonical_project_name(str(item.get("project"))) if item.get("project") else None,
                summary=(
                    f"benchmark execution handoff {run_group_id}: "
                    f"{item.get('ready_run_count', 0)}/{item.get('run_count', 0)} runs ready"
                ),
                payload=item,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", "execution handoff is a non-executing benchmark handoff"),
                ),
            )
        )
        edges.append(
            EvidenceEdge(
                handoff_id,
                f"machine-benchmark-manifest-group:{run_group_id}",
                "execution_handoff_prioritizes_manifest_group",
                "ranked execution handoff points at an exportable manifest group",
                1.0,
            )
        )
        if candidate_id:
            edges.append(
                EvidenceEdge(
                    handoff_id,
                    candidate_id,
                    "execution_handoff_for_candidate",
                    "ranked execution handoff was generated for candidate",
                    1.0,
                )
            )


def _add_machine_below_export_handoff_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    end: date,
    episode_ids: dict[tuple[str, str, str, str], str],
    episode_bound_ids: dict[tuple[str, str, str, str], str],
    artifact_name: str,
) -> None:
    for item in _machine_rows(payload, "items"):
        begin = _machine_dt(item.get("begin"))
        export_end = _machine_dt(item.get("end")) or begin
        if begin is None or not _machine_overlaps(begin, export_end, start=start, end=end):
            continue
        capture_id = str(item.get("capture_id") or begin.isoformat())
        episode_kind = str(item.get("episode_kind") or "pressure")
        node_id = f"machine-below-export-handoff:{capture_id}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_below_export_handoff_item",
                source="below",
                date=logical_date(begin),
                project=None,
                start=begin,
                end=export_end,
                summary=f"below export handoff: {episode_kind} in {capture_id}",
                payload={
                    "capture_id": capture_id,
                    "episode_kind": item.get("episode_kind"),
                    "host": item.get("host"),
                    "episode_started_at": item.get("episode_started_at"),
                    "episode_ended_at": item.get("episode_ended_at"),
                    "begin": item.get("begin"),
                    "end": item.get("end"),
                    "severity": item.get("severity"),
                    "confidence": item.get("confidence"),
                    "reason": item.get("reason"),
                },
                provenance=EvidenceProvenance("below", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("below", "partial", "handoff item is a non-executing live below export handoff"),
                ),
            )
        )
        target_id = episode_ids.get(_machine_queue_episode_key(item)) or episode_bound_ids.get(_machine_queue_bounds_key(item))
        if target_id is not None:
            edges.append(
                EvidenceEdge(
                    node_id,
                    target_id,
                    "below_export_handoff_targets_episode",
                    "live below export handoff targets a residual machine pressure episode",
                    _bounded_weight(item.get("severity"), None),
                )
            )


def _add_machine_manifest_diagnostic_nodes(
    nodes: list[EvidenceNode],
    payload: object,
    *,
    start: date,
    artifact_name: str,
) -> None:
    if not isinstance(payload, dict):
        return
    summary_payload = {
        key: payload.get(key)
        for key in (
            "root",
            "root_exists",
            "manifest_count",
            "source_loadable_count",
            "controlled_benchmark_valid_count",
            "validation_issue_count",
            "promotion_issue_count",
            "controlled_run_invalid_count",
            "ad_hoc_observational_count",
            "by_kind",
        )
    }
    nodes.append(
        EvidenceNode(
            id="machine-experiment-manifest-diagnostics:summary",
            kind="machine_experiment_manifest_diagnostics",
            source="machine",
            date=start,
            project=None,
            summary=(
                "experiment manifests: "
                f"{payload.get('source_loadable_count', 0)}/{payload.get('manifest_count', 0)} source-loadable, "
                f"{payload.get('controlled_benchmark_valid_count', 0)} controlled-valid"
            ),
            payload=summary_payload,
            provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
            caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in payload.get("caveats", ()) if c),
        )
    )
    for row in _machine_rows(payload, "diagnostics"):
        promotion_issue = row.get("in_window") is not False and not row.get("source_loadable")
        if row.get("controlled_benchmark_valid") or promotion_issue:
            node_id = f"machine-experiment-manifest:{row.get('relative_path') or row.get('path')}"
            nodes.append(
                EvidenceNode(
                    id=node_id,
                    kind="machine_experiment_manifest_diagnostic",
                    source="machine",
                    date=_date_value(row.get("started_at")) or start,
                    project=None,
                    summary=(
                        f"{row.get('manifest_kind')}: "
                        f"source_loadable={row.get('source_loadable')} "
                        f"controlled_valid={row.get('controlled_benchmark_valid')}"
                    ),
                    payload=row,
                    provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                    caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("issues", ()) if c),
                )
            )


def _add_machine_support_assessment_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "assessments"):
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        node_id = f"machine-support-assessment:{row.get('assessment_id')}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_support_assessment",
                source="machine",
                date=start,
                project=project,
                summary=str(row.get("summary") or "machine support assessment"),
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        candidate_id = row.get("candidate_id")
        if candidate_id:
            edges.append(EvidenceEdge(node_id, str(candidate_id), "support_assessment_for_candidate", "support/refusal assessment was generated from candidate", 1.0))
        if row.get("support_level") == "insufficient":
            refusal_id = f"machine-refusal-claim:{row.get('assessment_id')}"
            nodes.append(
                EvidenceNode(
                    id=refusal_id,
                    kind="analysis_claim",
                    source="machine",
                    date=start,
                    project=project,
                    summary=str(row.get("summary") or "machine attribution claim refused"),
                    payload={
                        "claim_type": "machine_attribution_refusal",
                        "support_level": "insufficient",
                        "confidence": row.get("confidence"),
                        "assessment_id": row.get("assessment_id"),
                        "candidate_id": row.get("candidate_id"),
                        "refusal_reasons": row.get("refusal_reasons", []),
                    },
                    provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                    caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("refusal_reasons", ()) if c),
                )
            )
            if candidate_id:
                edges.append(EvidenceEdge(refusal_id, str(candidate_id), "refusal_resolves_candidate", "support assessment refused this candidate", 1.0))


def _add_machine_attribution_claim_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "claims"):
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        claim_id = str(row.get("claim_id") or "")
        if not claim_id:
            continue
        support = str(row.get("support_level") or "unknown")
        caveats = tuple(str(c) for c in row.get("caveats", ()) if c)
        nodes.append(
            EvidenceNode(
                id=f"machine-attribution-claim:{claim_id}",
                kind="analysis_claim",
                source="machine",
                date=_machine_claim_date(row) or start,
                project=project,
                summary=str(row.get("summary") or "machine attribution claim"),
                payload={
                    "claim_type": row.get("claim_type") or "machine_attribution",
                    "support_level": support,
                    "confidence": row.get("confidence"),
                    "score": row.get("score"),
                    "source_ids": row.get("source_ids") or [],
                    "payload": row.get("payload") or {},
                },
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=tuple(EvidenceCaveat("machine", "partial", c) for c in caveats),
            )
        )
        source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), list) else ()
        for source_id in source_ids:
            source_text = str(source_id)
            if source_text.startswith("machine-candidate:") or source_text.startswith("cand"):
                relation = "claim_resolves_candidate" if support != "insufficient" else "refusal_resolves_candidate"
                edges.append(
                    EvidenceEdge(
                        f"machine-attribution-claim:{claim_id}",
                        source_text,
                        relation,
                        "machine attribution claim resolves upstream candidate",
                        1.0,
                    )
                )


def _add_machine_mechanism_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "mechanisms"):
        projects = tuple(
            project
            for project in (canonical_project_name(str(value)) for value in row.get("projects", ()) if value)
            if project is not None
        )
        if selected and not set(projects).intersection(selected):
            continue
        mechanism_id = str(row.get("mechanism_id") or "")
        if not mechanism_id:
            continue
        nodes.append(
            EvidenceNode(
                id=mechanism_id,
                kind="machine_mechanism_hypothesis",
                source="machine",
                date=start,
                project=projects[0] if len(projects) == 1 else None,
                summary=f"{row.get('mechanism_family')}: {row.get('current_support_ceiling')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", "mechanism is a falsifiable hypothesis, not a support upgrade"),
                ),
            )
        )
        candidate_ids = row.get("candidate_ids") if isinstance(row.get("candidate_ids"), list) else ()
        for candidate_id in candidate_ids:
            edges.append(EvidenceEdge(mechanism_id, str(candidate_id), "mechanism_explains_candidate", "mechanism hypothesis groups candidate rows", 1.0))
        assessment_ids = row.get("assessment_ids") if isinstance(row.get("assessment_ids"), list) else ()
        for assessment_id in assessment_ids:
            edges.append(EvidenceEdge(mechanism_id, f"machine-support-assessment:{assessment_id}", "mechanism_summarizes_assessment", "mechanism hypothesis summarizes support assessments", 1.0))


def _add_machine_negative_control_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "controls"):
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        control_id = str(row.get("control_id") or "")
        if not control_id:
            continue
        node_id = f"machine-negative-control:{control_id}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_negative_control",
                source="machine",
                date=start,
                project=project,
                summary=f"{row.get('status')}: {row.get('control_kind')} for {row.get('boundary_id')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )
        boundary_id = row.get("boundary_id")
        if boundary_id:
            edges.append(EvidenceEdge(node_id, str(boundary_id), "negative_control_checks_candidate", "negative control probes a boundary candidate", 1.0))


def _add_machine_calibration_nodes(
    nodes: list[EvidenceNode],
    payload: object,
    *,
    start: date,
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "fixtures"):
        fixture_id = str(row.get("fixture_id") or "")
        if not fixture_id:
            continue
        nodes.append(
            EvidenceNode(
                id=fixture_id,
                kind="machine_calibration_fixture",
                source="machine",
                date=start,
                project="sinity-lynchpin",
                summary=f"{row.get('status')}: {row.get('fixture_kind')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=tuple(EvidenceCaveat("machine", "partial", str(c)) for c in row.get("caveats", ()) if c),
            )
        )


def _add_machine_measurement_nodes(
    nodes: list[EvidenceNode],
    payload: object,
    *,
    start: date,
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "checks"):
        check_id = str(row.get("check_id") or "")
        if not check_id:
            continue
        nodes.append(
            EvidenceNode(
                id=check_id,
                kind="machine_measurement_check",
                source="machine",
                date=start,
                project="sinity-lynchpin",
                summary=f"{row.get('status')}: {row.get('check_kind')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", str(row.get("support_consequence"))),
                ) if row.get("support_consequence") else (),
            )
        )


def _add_machine_instrumentation_gap_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    for row in _machine_rows(payload, "gaps"):
        project = canonical_project_name(str(row.get("project"))) if row.get("project") else None
        if selected and project not in selected:
            continue
        gap_id = str(row.get("gap_id") or "")
        if not gap_id:
            continue
        nodes.append(
            EvidenceNode(
                id=gap_id,
                kind="machine_instrumentation_gap",
                source="machine",
                date=start,
                project=project,
                summary=f"{row.get('missing_source')}: {row.get('missing')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", "gap blocks attribution support until measured or bypassed by design"),
                ),
            )
        )
        mechanism_id = row.get("mechanism_id")
        if mechanism_id:
            edges.append(EvidenceEdge(gap_id, str(mechanism_id), "instrumentation_gap_blocks_mechanism", "missing measurement blocks mechanism support upgrade", 1.0))
        assessment_id = row.get("assessment_id")
        if assessment_id:
            edges.append(EvidenceEdge(gap_id, f"machine-support-assessment:{assessment_id}", "instrumentation_gap_blocks_assessment", "missing measurement explains support refusal", 1.0))
        candidate_id = row.get("candidate_id")
        if candidate_id:
            edges.append(EvidenceEdge(gap_id, str(candidate_id), "instrumentation_gap_blocks_candidate", "missing measurement blocks candidate upgrade", 1.0))


def _add_machine_assumption_check_nodes(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    payload: object,
    *,
    start: date,
    selected: set[str],
    artifact_name: str,
) -> None:
    del selected
    for row in _machine_rows(payload, "checks"):
        assumption_id = str(row.get("assumption_id") or "")
        if not assumption_id:
            continue
        node_id = f"machine-assumption-check:{assumption_id}"
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="machine_assumption_check",
                source="machine",
                date=start,
                project=None,
                summary=f"{row.get('check_status')}: {row.get('assumption')}",
                payload=row,
                provenance=EvidenceProvenance("machine", "materialized", path=artifact_name),
                caveats=(
                    EvidenceCaveat("machine", "partial", str(row.get("support_consequence"))),
                ) if row.get("support_consequence") else (),
            )
        )
        claim_id = row.get("claim_id")
        if claim_id:
            edges.append(EvidenceEdge(node_id, f"machine-attribution-claim:{claim_id}", "assumption_check_limits_claim", "assumption check constrains claim support", 1.0))


def _machine_claim_date(row: dict[str, Any]) -> date | None:
    value = row.get("date")
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _load_machine_graph_artifact(name: str) -> dict[str, Any] | None:
    payload, _materialization = load_materialized_analysis_artifact(name)
    return payload if isinstance(payload, dict) else None


def _machine_rows(payload: object, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get(key)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _dict_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _machine_manifest_validation_status(payload: dict[str, Any]) -> str | None:
    if payload.get("valid") is True:
        return "valid"
    if payload.get("valid") is False:
        return "invalid"
    if "valid" in payload:
        return "unknown"
    status = payload.get("status") or payload.get("validation_status")
    return str(status) if status else None


def _machine_embedded_rows(row: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = row.get(key)
    if not isinstance(rows, list):
        return []
    return [embedded for embedded in rows if isinstance(embedded, dict)]


def _machine_payload_overlaps(payload: object, *, start: date, end: date) -> bool:
    if not isinstance(payload, dict):
        return False
    generated_for = payload.get("generated_for")
    if not isinstance(generated_for, dict):
        return True
    payload_start = _date_value(generated_for.get("start"))
    payload_end = _date_value(generated_for.get("end"))
    if payload_start is None or payload_end is None:
        return True
    return payload_end >= start and payload_start <= end


def _selected_machine_episode_keys(
    *,
    context_payload: object,
    claims_payload: object,
    selected: set[str],
) -> set[tuple[str, str, str, str]]:
    if not selected:
        return set()
    keys: set[tuple[str, str, str, str]] = set()
    for row in _machine_rows(context_payload, "windows"):
        projects = {
            project
            for project in (canonical_project_name(str(value)) for value in row.get("projects", ()) if value)
            if project is not None
        }
        if not projects.intersection(selected):
            continue
        keys.update(_machine_episode_key(embedded) for embedded in _machine_embedded_rows(row, "episodes"))
    for row in _machine_rows(claims_payload, "claim_packs"):
        project = _project_from_path(row.get("git_root") or row.get("cwd"))
        if project not in selected:
            continue
        keys.update(_machine_episode_key(embedded) for embedded in _machine_embedded_rows(row, "episodes"))
    return keys


def _machine_dt(value: object) -> datetime | None:
    if value is None:
        return None
    return parse_datetime(str(value))


def _date_value(value: object) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        parsed = _machine_dt(value)
        return parsed.date() if parsed is not None else None


def _machine_overlaps(started_at: datetime, ended_at: datetime | None, *, start: date, end: date) -> bool:
    row_end = ended_at or started_at
    return row_end.date() >= start and started_at.date() <= end


def _machine_episode_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("kind") or ""),
        str(row.get("host") or ""),
        str(row.get("started_at") or ""),
        str(row.get("subject") or ""),
    )


def _machine_episode_bounds_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("kind") or ""),
        str(row.get("host") or ""),
        str(row.get("started_at") or ""),
        str(row.get("ended_at") or ""),
    )


def _machine_attribution_episode_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("episode_kind") or ""),
        str(row.get("host") or ""),
        str(row.get("episode_started_at") or ""),
        "",
    )


def _machine_attribution_bounds_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("episode_kind") or ""),
        str(row.get("host") or ""),
        str(row.get("episode_started_at") or ""),
        str(row.get("episode_ended_at") or ""),
    )


def _machine_queue_episode_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("episode_kind") or ""),
        str(row.get("host") or ""),
        str(row.get("episode_started_at") or ""),
        "",
    )


def _machine_queue_bounds_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("episode_kind") or ""),
        str(row.get("host") or ""),
        str(row.get("episode_started_at") or ""),
        str(row.get("episode_ended_at") or ""),
    )


def _candidate_source_targets(source_id: str) -> tuple[str, ...]:
    if source_id.startswith("machine-"):
        return (source_id,)
    return (
        f"machine-mining-scan:{source_id}",
        f"machine-observation-cohort:{source_id}",
        f"machine-cohort-contrast:{source_id}",
        f"machine-boundary:{source_id}",
        f"machine-matched-design:{source_id}",
        f"machine-negative-control:{source_id}",
        f"machine-lagged-exposure:{source_id}",
        f"machine-anomaly-cluster:{source_id}",
    )


def _work_failure_signature(row: dict[str, Any]) -> str:
    failure_kind = str(row.get("failure_kind") or "unknown")
    project = str(row.get("project")) if row.get("project") else None
    package = str(row.get("package")) if row.get("package") else None
    stage = str(row.get("stage_name")) if row.get("stage_name") else None
    status = str(row.get("status")) if row.get("status") else None
    failure_type = str(row.get("failure_type")) if row.get("failure_type") else None
    exit_code = str(row.get("exit_code")) if row.get("exit_code") is not None else None
    locus = package or stage or project or "unknown"
    return ":".join(part for part in (failure_kind, locus, status, failure_type, exit_code) if part)


def _machine_episode_id(row: dict[str, Any]) -> str:
    kind, host, started, subject = _machine_episode_key(row)
    return f"machine-episode:{host}:{kind}:{started}:{subject}"


def _bounded_weight(numerator: object, denominator: object | None) -> float:
    try:
        value = float(str(numerator or 0.0))
        base = float(str(denominator or 0.0))
    except (TypeError, ValueError):
        return 0.5
    if base <= 0:
        return 0.7 if value > 0 else 0.4
    return max(0.1, min(1.0, value / base))


def _project_from_path(value: object) -> str | None:
    if not value:
        return None
    return canonical_project_name(Path(str(value)).name)
