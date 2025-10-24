from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.graph.evidence_graph import build_evidence_graph
from lynchpin.graph.machine_analysis import add_machine_analysis_nodes
from tests.graph.evidence_graph_fixtures import (  # noqa: F401
    _mock_empty_sources,
    _no_analysis_claims,
    _no_substrate_overlap,
)

UTC = timezone.utc


def test_build_evidence_graph_surfaces_analysis_artifacts(monkeypatch, tmp_path):
    generated = datetime(2026, 5, 4, 10, tzinfo=UTC)
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifact = SimpleNamespace(
        name="sinex_structure_metrics.json",
        path=tmp_path / "sinex_structure_metrics.json",
        kind="json",
        project="sinex",
        projects=("sinex",),
        size_bytes=42,
        modified_at=modified,
        generated_at=generated,
        top_level_keys=("generated_at_utc", "totals"),
        brief="sinex summary",
        references=(),
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts",
        lambda **kwargs: (artifact,),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinex",),
    )

    analysis_nodes = [n for n in graph.nodes if n.kind == "analysis_artifact"]
    assert len(analysis_nodes) == 1
    node = analysis_nodes[0]
    assert node.source == "analysis"
    assert node.project == "sinex"
    assert node.date == date(2026, 5, 5)
    assert node.payload["top_level_keys"] == ("generated_at_utc", "totals")
    assert node.payload["brief"] == "sinex summary"
    assert node.provenance.path.endswith("sinex_structure_metrics.json")


def test_build_evidence_graph_surfaces_machine_artifacts_as_nodes(
    monkeypatch, tmp_path
):
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    (analysis_root / "machine_episode_analysis.json").write_text(
        """{
          "episodes": [
            {
              "kind": "io_pressure",
              "host": "host",
              "started_at": "2026-05-05T12:00:00+00:00",
              "ended_at": "2026-05-05T12:10:00+00:00",
              "subject": null,
              "severity": 0.8,
              "confidence": 0.9,
              "sample_count": 4,
              "sources": ["machine_metric_sample"],
              "evidence": [],
              "payload": {},
              "caveats": []
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_context_windows.json").write_text(
        """{
          "windows": [
            {
              "window_id": "w1",
              "started_at": "2026-05-05T12:01:00+00:00",
              "ended_at": "2026-05-05T12:03:00+00:00",
              "projects": ["sinity-lynchpin"],
              "source": "terminal_session",
              "work_kind": "test",
              "summary": "pytest run",
              "duration_seconds": 120,
              "episode_count": 1,
              "overlap_seconds": 120,
              "interpretation": "observed overlap with io_pressure",
              "episodes": [
                {
                  "kind": "io_pressure",
                  "host": "host",
                  "started_at": "2026-05-05T12:00:00+00:00",
                  "ended_at": "2026-05-05T12:10:00+00:00",
                  "subject": null,
                  "overlap_seconds": 120
                }
              ],
              "caveats": ["observational"]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_observational_baselines.json").write_text(
        """{
          "work_context": [
            {
              "dimension": "project",
              "key": "sinity-lynchpin",
              "window_count": 1,
              "windows_with_episodes": 1,
              "episode_overlap_rate": 1.0,
              "caveats": ["derived"]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_work_observations.json").write_text(
        """{
          "daily": [
            {
              "date": "2026-05-05",
              "work_kind": "xtask_invocation",
              "project": "sinity-lynchpin",
              "command": ["pytest"],
              "observation_count": 1,
              "success_count": 1,
              "failed_count": 0,
              "median_duration_s": 120
            }
          ],
          "stage_summaries": [
            {"stage_name": "test", "observation_count": 2, "p95_duration_s": 20, "max_duration_s": 30}
          ],
          "test_summaries": [
            {"package": "lynchpin", "status": "pass", "test_count": 2, "p95_duration_s": 10, "max_duration_s": 12}
          ],
          "failure_summaries": [
            {"failure_kind": "test", "project": "sinity-lynchpin", "package": "lynchpin", "status": "fail", "failure_type": "assertion", "failure_count": 1}
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_mining.json").write_text(
        """{
          "scan": {
            "scan_id": "scan1",
            "comparison_universe_size": 1,
            "caveats": []
          },
          "cohorts": [
            {
              "cohort_id": "cohort1",
              "scan_id": "scan1",
              "dimensions": {"project": "sinity-lynchpin", "stage_name": "test"},
              "row_count": 4,
              "caveats": []
            }
          ],
          "lagged_exposures": [
            {
              "summary_id": "machine-lagged-exposure:lag1",
              "dimensions": {"project": "sinity-lynchpin", "stage_name": "test"},
              "pressure_metric": "host_io_pressure_some_avg10_max",
              "median_delta": 3.0,
              "caveats": ["exploratory"]
            }
          ],
          "anomaly_clusters": [
            {
              "cluster_id": "machine-anomaly-cluster:cluster1",
              "dimensions": {"project": "sinity-lynchpin", "stage_name": "test"},
              "anomaly_count": 2,
              "max_outcome": 42.0,
              "caveats": ["tail only"]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_attribution_candidates.json").write_text(
        """{
          "candidate_count": 1,
          "candidates": [
            {
              "candidate_id": "machine-candidate:c1",
              "project": "sinity-lynchpin",
              "metric": "stage.duration_s",
              "suspected_factor": "cohort_contrast:stage=test",
              "mechanism_family": "observational_stage_contrast",
              "support_ceiling": "candidate",
              "priority_score": 12.0,
              "score_components": {"effect_size": 3.0, "recurrence": 4.0},
              "summary": "candidate from mined cohort",
              "source_artifacts": ["machine_mining.json"],
              "source_ids": ["cohort1", "machine-work-failure-summary:test:lynchpin:fail:assertion"],
              "suggested_benchmark_manifest": {
                "controlled_benchmark": {
                  "run_group_id": "<fill-run-group-id>",
                  "cache_conditions": ["cold", "warm"]
                }
              },
              "caveats": ["candidate only"]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_plans.json").write_text(
        """{
          "plans": [
            {
              "plan_id": "plan1",
              "candidate_id": "machine-candidate:c1",
              "planning_status": "ready",
              "primary_metric": "stage.duration_s",
              "manifest_preview": {"candidate": {"candidate_id": "machine-candidate:c1"}},
              "caveats": []
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_manifest_bundle.json").write_text(
        """{
          "groups": [
            {
              "run_group_id": "grp1",
              "plan_id": "plan1",
              "candidate_id": "machine-candidate:c1",
              "planning_status": "ready",
              "support_ceiling": "controlled",
              "primary_metric": "stage.duration_s",
              "run_count": 1,
              "run_templates": [
                {
                  "run_id": "run-template-1",
                  "sequence_index": 1,
                  "treatment_label": "baseline",
                  "cache_condition": "warm",
                  "derivation_key": "/nix/store/demo.drv",
                  "telemetry_window_id": "grp1:run-template-1:manifest_timestamps"
                }
              ],
              "caveats": []
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_preflight.json").write_text(
        """{
          "groups": [
            {
              "run_group_id": "grp1",
              "runs": [
                {
                  "run_id": "run-template-1",
                  "ready_to_export": true,
                  "issues": [],
                  "warnings": ["internal-json path is templated until export"]
                }
              ]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_execution_handoff.json").write_text(
        """{
          "items": [
            {
              "handoff_id": "machine-benchmark-handoff:grp1",
              "candidate_id": "machine-candidate:c1",
              "run_group_id": "grp1",
              "plan_id": "plan1",
              "ready_to_export": true,
              "run_count": 1,
              "ready_run_count": 1,
              "next_action": "execute the approved manifest and promote run logs/telemetry"
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_claims.json").write_text(
        """{
          "claim_packs": [
            {
              "run_id": "run1",
              "run_group_id": "grp1",
              "claim_mode": "manifest_observational",
              "workload": "pytest",
              "git_root": "/realm/project/sinity-lynchpin",
              "started_at": "2026-05-05T12:02:00+00:00",
              "ended_at": "2026-05-05T12:04:00+00:00",
              "duration_seconds": 120,
              "manifest_validation": {
                "valid": false,
                "issues": ["measurement_context.system_generation missing"],
                "warnings": ["planned_treatment not controlled-ready: missing fixed derivation set"]
              },
              "episodes": [
                {
                  "kind": "io_pressure",
                  "host": "host",
                  "started_at": "2026-05-05T12:00:00+00:00",
                  "ended_at": "2026-05-05T12:10:00+00:00",
                  "subject": null,
                  "overlap_seconds": 120
                }
              ],
              "internal_json": {
                "phases": [
                  {
                    "activity_id": "a1",
                    "name": "build pytest",
                    "duration_seconds": 1.2,
                    "status": "complete"
                  }
                ]
              },
              "caveats": ["observational"]
            }
          ],
          "effect_estimates": [
            {
              "run_group_id": "grp1",
              "metric": "duration_seconds",
              "estimator": "unpaired_bootstrap_mean_delta",
              "delta": 1.0
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_manifest_diagnostics.json").write_text(
        """{
          "manifest_count": 1,
          "source_loadable_count": 1,
          "controlled_benchmark_valid_count": 1,
          "validation_issue_count": 0,
          "promotion_issue_count": 0,
          "controlled_run_invalid_count": 0,
          "ad_hoc_observational_count": 0,
          "by_kind": {"executed_run": 1},
          "diagnostics": [
            {
              "relative_path": "grp1/runs/run1/manifest.json",
              "manifest_kind": "executed_run",
              "source_loadable": true,
              "controlled_benchmark_valid": true,
              "started_at": "2026-05-05T12:02:00+00:00",
              "issues": []
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_support_assessment.json").write_text(
        """{
          "assessments": [
            {
              "assessment_id": "assess1",
              "candidate_id": "machine-candidate:c1",
              "project": "sinity-lynchpin",
              "support_level": "insufficient",
              "summary": "controlled benchmark missing",
              "refusal_reasons": ["ready manifests exist but no executed controlled run exists"],
              "mechanism": {"mechanism_id": "machine-mechanism:resource_contention"}
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_mechanism_hypotheses.json").write_text(
        """{
          "mechanisms": [
            {
              "mechanism_id": "machine-mechanism:resource_contention",
              "mechanism_family": "resource_contention",
              "current_support_ceiling": "candidate",
              "projects": ["sinity-lynchpin"],
              "candidate_ids": ["machine-candidate:c1"],
              "assessment_ids": ["assess1"]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_instrumentation_gaps.json").write_text(
        """{
          "gaps": [
            {
              "gap_id": "machine-gap:controlled-run",
              "candidate_id": "machine-candidate:c1",
              "assessment_id": "assess1",
              "mechanism_id": "machine-mechanism:resource_contention",
              "project": "sinity-lynchpin",
              "missing_source": "controlled_benchmark_run",
              "missing": "executed_controlled_run"
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_attribution_claims.json").write_text(
        """{
          "claims": [
            {
              "claim_id": "claim1",
              "project": "sinity-lynchpin",
              "support_level": "insufficient",
              "summary": "claim refused pending controlled run",
              "source_ids": ["machine-candidate:c1"]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_assumption_checks.json").write_text(
        """{
          "checks": [
            {
              "assumption_id": "assumption1",
              "claim_id": "claim1",
              "check_status": "failed",
              "assumption": "controlled run exists",
              "support_consequence": "support remains insufficient"
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_below_attribution.json").write_text(
        """{
          "attributions": [],
          "workload_resource_attributions": [
            {
              "episode_kind": "io_pressure",
              "host": "host",
              "episode_started_at": "2026-05-05T12:00:00+00:00",
              "episode_ended_at": "2026-05-05T12:10:00+00:00",
              "severity": 0.8,
              "confidence": 0.9,
              "work_source": "xtask",
              "work_source_id": "xtask:run1",
              "project": "sinity-lynchpin",
              "work_started_at": "2026-05-05T12:01:00+00:00",
              "work_ended_at": "2026-05-05T12:03:00+00:00",
              "overlap_seconds": 120,
              "attribution_basis": ["host_io_pressure_some_avg10_max"],
              "caveats": ["observational"]
            }
          ]
        }""",
        encoding="utf-8",
    )
    (analysis_root / "machine_below_export_handoff.json").write_text(
        """{
          "planned_window_count": 1,
          "root": "/realm/data/captures/stability-lab",
          "items": [
            {
              "episode_kind": "io_pressure",
              "host": "host",
              "episode_started_at": "2026-05-05T12:00:00+00:00",
              "episode_ended_at": "2026-05-05T12:10:00+00:00",
              "severity": 0.8,
              "confidence": 0.9,
              "begin": "2026-05-05T11:59:00+00:00",
              "end": "2026-05-05T12:11:00+00:00",
              "capture_id": "pressure-io-20260505T120000",
              "reason": "residual pressure episode lacks bounded below capture"
            }
          ]
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lynchpin.core.io.get_config",
        lambda: type("Cfg", (), {"analysis_output_dir": analysis_root})(),
    )

    nodes = []
    edges = []
    add_machine_analysis_nodes(
        nodes,
        edges,
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        selected={"sinity-lynchpin"},
        exclude_names=frozenset(),
    )

    kinds = {node.kind for node in nodes}
    assert "machine_episode" in kinds
    assert "machine_context_window" in kinds
    assert "machine_baseline" in kinds
    assert "machine_work_observation" in kinds
    assert "machine_work_stage_summary" in kinds
    assert "machine_work_test_summary" in kinds
    assert "machine_work_failure_summary" in kinds
    assert "machine_observation_cohort" in kinds
    assert "machine_lagged_exposure_summary" in kinds
    assert "machine_anomaly_cluster" in kinds
    assert "machine_attribution_candidate" in kinds
    assert "machine_benchmark_plan" in kinds
    assert "machine_benchmark_manifest_group" in kinds
    assert "machine_benchmark_run_template" in kinds
    assert "machine_benchmark_preflight_run" in kinds
    assert "machine_benchmark_execution_handoff_item" in kinds
    assert "machine_below_export_handoff_item" in kinds
    assert "machine_workload_resource_attribution" in kinds
    assert "machine_benchmark_run" in kinds
    assert "machine_benchmark_phase" in kinds
    assert "machine_benchmark_estimate" in kinds
    assert "machine_experiment_claim" in kinds
    assert "machine_experiment_manifest_diagnostics" in kinds
    assert "machine_experiment_manifest_diagnostic" in kinds
    assert "machine_support_assessment" in kinds
    assert "machine_mechanism_hypothesis" in kinds
    assert "machine_instrumentation_gap" in kinds
    assert "machine_assumption_check" in kinds
    assert any(edge.relation == "overlaps_machine_pressure" for edge in edges)
    assert any(edge.relation == "candidate_from_artifact" for edge in edges)
    assert any(edge.relation == "manifest_group_from_plan" for edge in edges)
    assert any(edge.relation == "run_template_in_manifest_group" for edge in edges)
    assert any(edge.relation == "preflight_checks_run_template" for edge in edges)
    assert any(edge.relation == "execution_handoff_prioritizes_manifest_group" for edge in edges)
    assert any(edge.relation == "execution_handoff_for_candidate" for edge in edges)
    assert any(edge.relation == "below_export_handoff_targets_episode" for edge in edges)
    assert any(edge.relation == "workload_resource_supports_episode" for edge in edges)
    assert any(edge.relation == "run_overlaps_machine_episode" for edge in edges)
    assert any(edge.relation == "phase_in_run" for edge in edges)
    assert any(edge.relation == "estimate_summarizes_runs" for edge in edges)
    run_node = next(node for node in nodes if node.id == "machine-benchmark-run:run1")
    assert run_node.payload["manifest_validation_status"] == "invalid"
    assert run_node.payload["manifest_validation_issues"] == (
        "measurement_context.system_generation missing",
    )
    assert any(edge.relation == "experiment_claim_support" for edge in edges)
    assert any(edge.relation == "support_assessment_for_candidate" for edge in edges)
    assert any(edge.relation == "mechanism_explains_candidate" for edge in edges)
    assert any(edge.relation == "mechanism_summarizes_assessment" for edge in edges)
    assert any(edge.relation == "instrumentation_gap_blocks_mechanism" for edge in edges)
    assert any(edge.relation == "instrumentation_gap_blocks_assessment" for edge in edges)
    assert any(edge.relation == "instrumentation_gap_blocks_candidate" for edge in edges)
    assert any(edge.relation == "assumption_check_limits_claim" for edge in edges)
    assert any(edge.relation == "refusal_resolves_candidate" for edge in edges)


def test_build_evidence_graph_excludes_named_analysis_artifacts(monkeypatch, tmp_path):
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifacts = (
        SimpleNamespace(
            name="current_state_context_pack.json",
            path=tmp_path / "current_state_context_pack.json",
            kind="json",
            project="sinity-lynchpin",
            projects=("sinity-lynchpin",),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("graph",),
            brief="previous current-state pack",
            references=(),
        ),
        SimpleNamespace(
            name="analysis_status.json",
            path=tmp_path / "analysis_status.json",
            kind="json",
            project="sinity-lynchpin",
            projects=("sinity-lynchpin",),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("families",),
            brief="status",
            references=("current_state_context_pack.json",),
        ),
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: artifacts
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinity-lynchpin",),
        exclude_analysis_artifacts=("current_state_context_pack.json",),
    )

    names = {
        node.payload["name"]
        for node in graph.nodes
        if node.kind == "analysis_artifact" and node.payload
    }
    assert names == {"analysis_status.json"}
    assert not graph.edges


def test_build_evidence_graph_surfaces_analysis_claims(monkeypatch, tmp_path):
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifact = SimpleNamespace(
        name="active_project_snapshot.json",
        path=tmp_path / "active_project_snapshot.json",
        kind="json",
        project="sinex",
        projects=("sinex",),
        size_bytes=42,
        modified_at=modified,
        generated_at=modified,
        top_level_keys=("projects",),
        brief="snapshot",
        references=(),
    )
    claim = SimpleNamespace(
        id="active-project-snapshot:sinex",
        artifact_name="active_project_snapshot.json",
        claim_type="project_snapshot",
        project="sinex",
        summary="sinex: 3 first-parent commits",
        payload={"recent_git": {"commit_count": 3}},
        confidence=0.82,
        generated_at=modified,
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts",
        lambda **kwargs: (artifact,),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.analysis_claims", lambda **kwargs: (claim,)
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinex",),
    )

    claim_node = next(node for node in graph.nodes if node.kind == "analysis_claim")
    assert claim_node.summary == "sinex: 3 first-parent commits"
    assert claim_node.payload["claim_type"] == "project_snapshot"
    assert any(
        edge.source_id == "analysis-claim:active-project-snapshot:sinex"
        and edge.target_id == "analysis:active_project_snapshot.json:sinex"
        and edge.relation == "references"
        for edge in graph.edges
    )


def test_build_evidence_graph_links_analysis_artifact_references(monkeypatch, tmp_path):
    modified = datetime(2026, 5, 5, 9, tzinfo=UTC)
    artifacts = (
        SimpleNamespace(
            name="analysis_status.json",
            path=tmp_path / "analysis_status.json",
            kind="json",
            project="sinity-lynchpin",
            projects=("sinity-lynchpin",),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("families",),
            brief="status",
            references=("cross_project_metrics.json",),
        ),
        SimpleNamespace(
            name="cross_project_metrics.json",
            path=tmp_path / "cross_project_metrics.json",
            kind="json",
            project=None,
            projects=("sinex", "polylogue", "sinity-lynchpin"),
            size_bytes=42,
            modified_at=modified,
            generated_at=None,
            top_level_keys=("projects",),
            brief="metrics",
            references=(),
        ),
    )
    monkeypatch.setattr("lynchpin.graph.evidence_git.commit_facts", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.session_profiles_for_date",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_polylogue.work_events", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_raw_log.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_clipboard.entries_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_irc.conversations_in_range", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.project_focus_days", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.deep_work", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.circadian", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.loops", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.fragmentation", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_activitywatch.attention", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_web_media.daily_browsing", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: ()
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_temporal_signals",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_system_signals.add_readiness",
        lambda nodes, **kwargs: None,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_analysis.latest_artifacts", lambda **kwargs: artifacts
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.source_readiness",
        lambda **kwargs: SimpleNamespace(caveats=()),
    )

    graph = build_evidence_graph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 5),
        projects=("sinity-lynchpin",),
    )

    assert any(
        edge.source_id == "analysis:analysis_status.json:sinity-lynchpin"
        and edge.target_id == "analysis:cross_project_metrics.json:sinity-lynchpin"
        and edge.relation == "references"
        for edge in graph.edges
    )
