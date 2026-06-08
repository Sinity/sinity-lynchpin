from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_machine_metrics_daily_materializes_machine_and_substrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_calls = []
    substrate_calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "machine", "status": "ready"}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_materialized(name, *, window=None):
        machine_calls.append((name, window))
        return Result()

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.best_materialized_refresh_id",
        lambda *_args, **_kwargs: "rid",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.machine.load_machine_metric_daily",
        lambda *_args, **_kwargs: [
            (date(2026, 5, 1), "host", 3, 40.0, 90.0, 80.0, 120.0, 0.1, 0.5, 1.0, 5.0, 0),
        ],
    )

    from lynchpin.mcp.tools.machine import machine_metrics_daily

    rows = machine_metrics_daily(start="2026-05-01", end="2026-05-03")

    assert machine_calls == [("machine", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert substrate_calls == [
        ("machine_metrics_daily", (date(2026, 5, 1), date(2026, 5, 4)))
    ]
    assert rows[0]["date"] == "2026-05-01"


def test_sinnix_generation_history_materializes_substrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.substrate.machine.load_sinnix_generation_rows",
        lambda *_args, **_kwargs: [
            ("host", "42", "2026-05-01T00:00:00+00:00", "/nix/store/x", "rev", "26.05"),
        ],
    )

    from lynchpin.mcp.tools.machine import sinnix_generation_history

    rows = sinnix_generation_history(limit=1)

    assert calls == [("sinnix_generation_history", None)]
    assert rows[0]["generation"] == "42"


def test_machine_bufferbloat_summary_materializes_and_selects_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_calls = []
    substrate_calls = []
    reader_calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "machine", "status": "ready"}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_materialized(name, *, window=None):
        machine_calls.append((name, window))
        return Result()

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    def fake_load_bufferbloat_daily(*_args, **kwargs):
        reader_calls.append(kwargs)
        return [(date(2026, 5, 1), "enp6s0", 1, 10.0, 10.0, 10.0, 0.0, 0.0, 0.0)]

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.best_materialized_refresh_id",
        lambda *_args, **_kwargs: "network-rid",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.machine.load_bufferbloat_daily",
        fake_load_bufferbloat_daily,
    )

    from lynchpin.mcp.tools.machine import machine_bufferbloat_summary

    result = machine_bufferbloat_summary(
        start="2026-05-01",
        end="2026-05-03",
        interface="enp6s0",
    )

    assert machine_calls == [("machine", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert substrate_calls == [
        ("machine_bufferbloat_summary", (date(2026, 5, 1), date(2026, 5, 4)))
    ]
    assert reader_calls == [
        {
            "refresh_id": "network-rid",
            "start": date(2026, 5, 1),
            "end": date(2026, 5, 3),
            "interface": "enp6s0",
        }
    ]
    assert result["summary"]["refresh_id"] == "network-rid"
    assert result["rows"][0]["avg_ms_p50"] == 10.0


def test_machine_service_state_summary_materializes_half_open_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_calls = []
    substrate_calls = []

    class Result:
        def to_json(self) -> dict[str, object]:
            return {"name": "machine", "status": "ready"}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_materialized(name, *, window=None):
        machine_calls.append((name, window))
        return Result()

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.best_materialized_refresh_id",
        lambda *_args, **_kwargs: "service-rid",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.machine.load_machine_service_state_summary",
        lambda *_args, **_kwargs: [
            ("host", "svc.service", "system", 1, 1, 1024, 10, 20, 30, None, None, 10, 20, 30),
        ],
    )

    from lynchpin.mcp.tools.machine import machine_service_state_summary

    rows = machine_service_state_summary(start="2026-05-01", end="2026-05-03")

    assert machine_calls == [("machine", (date(2026, 5, 1), date(2026, 5, 4)))]
    assert substrate_calls == [
        ("machine_service_state_summary", (date(2026, 5, 1), date(2026, 5, 4)))
    ]
    assert rows[0]["unit"] == "svc.service"


def test_machine_work_observation_daily_materializes_and_selects_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    substrate_calls = []
    reader_calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    def fake_daily_work_observation_series(*_args, **kwargs):
        reader_calls.append(kwargs)
        return [
            SimpleNamespace(
                date=date(2026, 5, 1),
                work_kind="xtask_invocation",
                project="sinex",
                command=("xtask", "check"),
                observation_count=1,
                success_count=1,
                failed_count=0,
                avg_duration_s=12.0,
                median_duration_s=12.0,
                p95_duration_s=12.0,
                max_duration_s=12.0,
            )
        ]

    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine._best_refresh_or_none",
        lambda *_args, **_kwargs: "work-rid",
    )
    monkeypatch.setattr(
        "lynchpin.analysis.machine.work_observations.daily_work_observation_series",
        fake_daily_work_observation_series,
    )

    from lynchpin.mcp.tools.machine import machine_work_observation_daily

    result = machine_work_observation_daily(
        start="2026-05-01",
        end="2026-05-03",
        project="sinex",
        command_contains="check",
    )

    assert substrate_calls == [
        ("machine_work_observation_daily", (date(2026, 5, 1), date(2026, 5, 3)))
    ]
    assert reader_calls == [
        {
            "refresh_id": "work-rid",
            "start": date(2026, 5, 1),
            "end": date(2026, 5, 3),
            "project": "sinex",
            "command_contains": "check",
        }
    ]
    assert result["summary"]["refresh_id"] == "work-rid"
    assert result["rows"][0]["command"] == ["xtask", "check"]


def test_machine_workflow_mechanics_materializes_and_passes_best_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    substrate_calls = []
    analysis_calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Report:
        def to_json(self) -> dict[str, object]:
            return {"invocation_count": 0, "retry_chain_count": 0}

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        substrate_calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    def fake_analyze_workflow_mechanics(**kwargs):
        analysis_calls.append(kwargs)
        return Report()

    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine._best_refresh_or_none",
        lambda *_args, **_kwargs: "work-rid",
    )
    monkeypatch.setattr(
        "lynchpin.analysis.workflow_mechanics.analyze_workflow_mechanics",
        fake_analyze_workflow_mechanics,
    )

    from lynchpin.mcp.tools.machine import machine_workflow_mechanics

    result = machine_workflow_mechanics(
        start="2026-05-01",
        end="2026-05-03",
        project="sinex",
        retry_gap_min=5,
        limit=10,
    )

    assert substrate_calls == [
        ("machine_workflow_mechanics", (date(2026, 5, 1), date(2026, 5, 3)))
    ]
    assert analysis_calls == [
        {
            "start": date(2026, 5, 1),
            "end": date(2026, 5, 3),
            "project": "sinex",
            "refresh_id": "work-rid",
            "retry_gap_min": 5,
            "limit": 10,
        }
    ]
    assert result["invocation_count"] == 0


def test_machine_workflow_mechanics_reuses_default_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_ensure_substrate_materialized_for_read(**_kwargs):
        raise AssertionError("default artifact reads should not materialize substrate")

    def fail_analyze_workflow_mechanics(**_kwargs):
        raise AssertionError("default artifact reads should not recompute analysis")

    monkeypatch.setattr(
        "lynchpin.mcp.tools.machine.ensure_substrate_materialized_for_read",
        fail_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr(
        "lynchpin.analysis.workflow_mechanics.analyze_workflow_mechanics",
        fail_analyze_workflow_mechanics,
    )
    monkeypatch.setattr(
        "lynchpin.core.io.load_materialized_analysis_artifact",
        lambda name: (
            {
                "generated_at_utc": "2026-06-06T00:00:00+00:00",
                "start": None,
                "end": None,
                "invocation_count": 17,
                "failure_count": 3,
                "retry_chain_count": 2,
                "command_summaries": [],
                "retry_chains": [],
            },
            {"name": "analysis_artifact", "status": "ready"},
        )
        if name == "workflow_mechanics.json"
        else (None, None),
    )

    from lynchpin.mcp.tools.machine import machine_workflow_mechanics

    result = machine_workflow_mechanics()

    assert result["source"] == "artifact"
    assert result["invocation_count"] == 17
    assert result["retry_chain_count"] == 2


def test_machine_analysis_mcp_tools_read_materialized_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    (analysis_root / "machine_episode_analysis.json").write_text(
        json.dumps({
            "detector_version": "sustained-pressure-v2",
            "episodes": [{"kind": "load_pressure", "host": "host", "started_at": "2026-05-01T12:00:00+00:00", "ended_at": "2026-05-01T12:05:00+00:00", "severity": 0.2}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_context_windows.json").write_text(
        json.dumps({"windows": [{"source": "polylogue_session", "window_id": "w1", "started_at": "2026-05-01T12:01:00+00:00", "ended_at": "2026-05-01T12:02:00+00:00", "projects": ["sinity-lynchpin"], "episode_count": 1}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_below_attribution.json").write_text(
        json.dumps({
            "episode_count": 1,
            "attributed_episode_count": 1,
            "pressure_episode_count": 1,
            "unattributed_pressure_episode_count": 0,
            "workload_resource_attributed_pressure_episode_count": 1,
            "residual_unattributed_pressure_episode_count": 0,
            "capture_count": 1,
            "caveats": [],
            "attributions": [{"episode_kind": "load_pressure", "capture_id": "cap1", "episode_started_at": "2026-05-01T12:00:00+00:00", "episode_ended_at": "2026-05-01T12:05:00+00:00", "overlap_seconds": 60.0, "severity": 0.2}],
            "workload_resource_attributions": [{"episode_kind": "load_pressure", "work_source_id": "xtask:1", "episode_started_at": "2026-05-01T12:00:00+00:00", "episode_ended_at": "2026-05-01T12:05:00+00:00", "overlap_seconds": 30.0, "severity": 0.2}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_telemetry_analysis.json").write_text(
        json.dumps({
            "coverage": {"sample_count": 10, "first_observed_at": "2026-05-01T00:00:00+00:00"},
            "daily": [{"day": "2026-05-01", "sample_count": 10, "avg_load_1m": 1.2}],
            "signals": [{"metric": "avg_load_1m", "trend": "flat"}],
            "hardware_regimes": [{"gpu_pcie_gen": 4, "gpu_pcie_width": 16, "sample_count": 10}],
            "correlations": [{"left": "avg_load_1m", "right": "avg_io_psi_some", "correlations": {}}],
            "caveats": ["sample"],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_below_analysis.json").write_text(
        json.dumps({
            "window_count": 1,
            "live_store": {"index_count": 2},
            "system": [{"capture_id": "cap1", "sample_count": 4, "avg_cpu_pct": 12.0}],
            "top_processes": [{"capture_id": "cap1", "key": "pytest", "avg_cpu_pct": 8.0}],
            "top_cgroups": [{"capture_id": "cap1", "key": "user.slice", "avg_cpu_pct": 6.0}],
            "top_process_count": 1,
            "top_cgroup_count": 1,
            "caveats": [],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_observational_baselines.json").write_text(
        json.dumps({"generated_for": {"metrics": ["load_1m"]}, "caveats": ["observational"], "by_hour": [{"key": "12", "sample_count": 10}], "by_source": [], "by_hardware_regime": [], "daily_signals": [{"metric": "p95_load_1m", "sample_count": 8}], "era_comparisons": [], "work_context": []}),
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_claims.json").write_text(
        json.dumps({
            "run_count": 1,
            "controlled_claim_count": 0,
            "observational_claim_count": 1,
            "caveats": ["no controlled claims"],
            "claim_packs": [{
                "run_id": "run1",
                "run_group_id": "grp1",
                "workload": "xtask",
                "claim_mode": "manifest_observational",
                "started_at": "2026-05-01T12:00:00+00:00",
                "manifest_validation": {
                    "valid": False,
                    "issues": ["measurement_context.system_generation missing"],
                    "warnings": ["planned_treatment not controlled-ready: missing fixed derivation set"],
                },
                "internal_json": {"phases": [{"activity_id": "a1", "name": "build xtask", "duration_seconds": 1.2}]},
            }],
            "effect_estimates": [{"run_group_id": "grp1", "metric": "duration_seconds", "delta": 1.0}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_analysis_feature_frames.json").write_text(
        json.dumps({"frame": {"frame_id": "frame1", "unit_type": "work_observation_stage", "row_count": 1, "outcome_metric": "stage.duration_s", "leakage_status": "ok", "rows": [{"unit_id": "stage1"}]}}),
        encoding="utf-8",
    )
    (analysis_root / "machine_mining.json").write_text(
        json.dumps({
            "scan": {"scan_id": "scan1", "comparison_universe_size": 3},
            "cohort_count": 1,
            "cohorts": [{"cohort_id": "cohort1", "row_count": 2, "dimensions": {"project": "sinex", "stage_name": "test"}}],
            "lagged_exposure_count": 1,
            "lagged_exposures": [{"summary_id": "lag1", "dimensions": {"project": "sinex", "stage_name": "test"}, "pressure_metric": "host_io_pressure_some_avg10_max"}],
            "anomaly_cluster_count": 1,
            "anomaly_clusters": [{"cluster_id": "cluster1", "dimensions": {"project": "sinex", "stage_name": "test"}, "anomaly_count": 2}],
            "caveats": ["exploratory"],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_dataset_diagnostics.json").write_text(
        json.dumps({
            "diagnostic_count": 2,
            "feature_audit": {"status": "ready_for_mining", "row_count": 8},
            "mining_audit": {"multiplicity_status": "registered", "comparison_universe_size": 3},
            "diagnostics": [{"diagnostic_id": "diag1", "diagnostic_kind": "feature_frame_coverage", "severity": "info"}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_validation_design.json").write_text(
        json.dumps({"boundary_count": 1, "split": {"split_id": "split1"}, "boundaries": [{"boundary_id": "boundary1", "boundary_type": "git_commit_transition", "dimensions": {"project": "sinex"}}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_matched_designs.json").write_text(
        json.dumps({"design_count": 1, "supportable_design_count": 1, "designs": [{"design_id": "design1", "boundary_id": "boundary1", "candidate_id": "cand1", "identification_status": "design_ready"}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_negative_controls.json").write_text(
        json.dumps({
            "control_count": 1,
            "by_status": {"passed": 1},
            "controls": [{
                "control_id": "neg1",
                "design_id": "design1",
                "boundary_id": "boundary1",
                "status": "passed",
            }],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_comparisons.json").write_text(
        json.dumps({
            "contrast_count": 1,
            "multiplicity_policy": "BH",
            "contrasts": [{"contrast_id": "contrast1", "statistical_signal": "screening_signal", "median_delta": 3.0}],
            "caveats": ["observational"],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_attribution_candidates.json").write_text(
        json.dumps({
            "candidate_count": 1,
            "pareto_frontier_count": 1,
            "pareto_frontier_ids": ["cand1"],
            "candidates": [{
                "candidate_id": "cand1",
                "project": "sinex",
                "validation_status": "design_ready",
                "mechanism_family": "stage_regression_or_workload_mix",
                "pareto_frontier": True,
                "priority_score": 8.4,
            }],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_plans.json").write_text(
        json.dumps({"plan_count": 1, "ready_plan_count": 0, "plans": [{"plan_id": "plan1", "candidate_id": "cand1", "planning_status": "needs_binding", "manifest_preview": {"controlled_benchmark": {"run_group_id": "grp1"}}}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_manifest_bundle.json").write_text(
        json.dumps({
            "group_count": 1,
            "run_template_count": 2,
            "groups": [{"run_group_id": "grp1", "run_templates": [{"run_id": "template1"}]}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_preflight.json").write_text(
        json.dumps({
            "run_count": 2,
            "ready_run_count": 2,
            "issue_count": 0,
            "warning_count": 2,
            "runs": [{"run_id": "template1", "run_group_id": "grp1", "ready": True}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_benchmark_execution_handoff.json").write_text(
        json.dumps({
            "planned_window_count": 1,
            "ready_group_count": 1,
            "blocked_group_count": 0,
            "run_template_count": 2,
            "ready_run_count": 2,
            "items": [{
                "handoff_id": "machine-benchmark-handoff:grp1",
                "candidate_id": "cand1",
                "run_group_id": "grp1",
                "ready_to_export": True,
            }],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_below_export_handoff.json").write_text(
        json.dumps({
            "planned_window_count": 1,
            "failed_capture_count": 1,
            "root": "/realm/data/captures/stability-lab",
            "live_store": "/realm/data/captures/machine/below/store",
            "failed_captures": [{"capture_id": "pressure-empty-1"}],
            "items": [{
                "capture_id": "pressure-load-1",
                "episode_kind": "load_pressure",
                "severity": 1.0,
                "begin": "2026-05-15 03:11:51+02:00",
                "end": "2026-05-15 03:20:35+02:00",
            }],
            "caveats": ["dry-run only"],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_manifest_diagnostics.json").write_text(
        json.dumps({
            "root": "/realm/data/captures/machine/experiments",
            "root_exists": True,
            "manifest_count": 2,
            "source_loadable_count": 2,
            "controlled_benchmark_valid_count": 1,
            "validation_issue_count": 1,
            "promotion_issue_count": 0,
            "controlled_run_invalid_count": 0,
            "legacy_observational_count": 1,
            "by_kind": {"executed_run": 1, "legacy_or_ad_hoc_run": 1},
            "diagnostics": [
                {
                    "relative_path": "grp1/runs/run1/manifest.json",
                    "manifest_kind": "executed_run",
                    "source_loadable": True,
                    "controlled_benchmark_valid": True,
                    "issues": [],
                },
                {
                    "relative_path": "legacy/manifest.json",
                    "manifest_kind": "legacy_or_ad_hoc_run",
                    "source_loadable": True,
                    "controlled_benchmark_valid": False,
                    "issues": ["missing measurement_context"],
                },
            ],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_derivation_inventory.json").write_text(
        json.dumps({"target_count": 1, "ready_target_count": 1, "targets": [{"project": "sinex", "attr": "xtask", "eval_status": "ready"}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_support_assessment.json").write_text(
        json.dumps({
            "assessment_count": 2,
            "refusal_count": 1,
            "controlled_claim_count": 0,
            "natural_experiment_support_count": 1,
            "assessments": [
                {"assessment_id": "assess1", "candidate_id": "cand1", "support_level": "insufficient"},
                {"assessment_id": "assess2", "candidate_id": "cand2", "support_level": "natural_experiment"},
            ],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_mechanism_hypotheses.json").write_text(
        json.dumps({
            "mechanism_count": 1,
            "mechanisms": [{
                "mechanism_id": "machine-mechanism:stage_regression_or_workload_mix",
                "candidate_ids": ["cand1"],
                "mechanism_family": "stage_regression_or_workload_mix",
            }],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_instrumentation_gaps.json").write_text(
        json.dumps({
            "gap_count": 1,
            "by_missing_source": {"controlled_benchmark_run": 1},
            "gaps": [{
                "gap_id": "gap1",
                "candidate_id": "cand1",
                "project": "sinex",
                "missing_source": "controlled_benchmark_run",
                "missing": "executed_controlled_run",
            }],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_attribution_claims.json").write_text(
        json.dumps({
            "claim_count": 2,
            "by_support_level": {"insufficient": 1, "natural_experiment": 1},
            "claims": [
                {
                    "claim_id": "claim1",
                    "project": "sinex",
                    "support_level": "insufficient",
                    "source_ids": ["assess1", "cand1"],
                    "payload": {"metric": "stage.duration_s"},
                },
                {
                    "claim_id": "claim2",
                    "project": "sinex",
                    "support_level": "natural_experiment",
                    "source_ids": ["assess2", "cand1", "design1"],
                    "payload": {"metric": "stage.duration_s"},
                },
            ],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_assumption_checks.json").write_text(
        json.dumps({
            "check_count": 2,
            "by_status": {"failed": 1, "passed": 1},
            "checks": [
                {"assumption_id": "a1", "claim_id": "claim1", "check_status": "failed"},
                {"assumption_id": "a2", "claim_id": "claim2", "check_status": "passed"},
            ],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_analysis_readiness.json").write_text(
        json.dumps({
            "tables": [{"table": "machine_metric_sample", "row_count": 10}],
            "artifacts": [{"artifact": "machine_experiment_claims.json", "present": True}],
            "dimensions": [{"dimension": "controlled_benchmark_claims", "status": "missing"}],
            "caveats": ["not controlled"],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_analysis_materialization_report.json").write_text(
        json.dumps({"step_count": 1, "by_status": {"success": 1}, "steps": [{"name": "x"}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_calibration_fixtures.json").write_text(
        json.dumps({"fixture_count": 1, "by_status": {"passed": 1}, "fixtures": [{"fixture_id": "fixture1", "fixture_kind": "null", "status": "passed"}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_measurement_system.json").write_text(
        json.dumps({"check_count": 1, "by_status": {"passed": 1}, "checks": [{"check_id": "measure1", "check_kind": "timer_resolution_clock_source", "status": "passed"}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_work_observations.json").write_text(
        json.dumps({
            "daily": [{"date": "2026-05-01", "project": "sinex", "command": ["xtask", "check"], "observation_count": 1}],
            "stage_summaries": [{"stage_name": "test", "observation_count": 2}],
            "test_summaries": [{"package": "sinex-primitives", "status": "pass", "test_count": 2}],
            "failure_summaries": [{"failure_kind": "test", "package": "sinex-primitives", "failure_count": 1}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_work_state_windows.json").write_text(
        json.dumps({
            "window_count": 1,
            "pressure_state_counts": {"io_pressure": 1},
            "work_state_counts": {"test_workload": 1},
            "hardware_regime_counts": {"gen4x16": 1},
            "repo_state_counts": {"sinex": 1},
            "windows": [{
                "window_id": "state1",
                "started_at": "2026-05-01T12:00:00+00:00",
                "ended_at": "2026-05-01T12:05:00+00:00",
                "pressure_state": "io_pressure",
                "work_state": "test_workload",
                "projects": ["sinex"],
            }],
            "caveats": [],
        }),
        encoding="utf-8",
    )
    (analysis_root / "command_performance_windows.json").write_text(
        json.dumps({
            "command_count": 1,
            "tools": [{"tool": "pytest", "command_count": 1, "pressure_overlap_count": 1}],
            "windows": [{
                "tool": "pytest",
                "project": "sinex",
                "command": ["pytest"],
                "duration_seconds": 12.0,
                "machine_pressure_state": "io_pressure",
            }],
            "caveats": [],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_observational_deltas.json").write_text(
        json.dumps({
            "cohort_count": 1,
            "delta_count": 1,
            "cohorts": [{"tool": "pytest", "work_state": "test_workload", "pressure_state": "io_pressure"}],
            "deltas": [{"tool": "pytest", "work_state": "test_workload", "pressure_state": "io_pressure", "median_delta_seconds": 3.0}],
            "caveats": ["observational"],
        }),
        encoding="utf-8",
    )
    (analysis_root / "devshell_performance.json").write_text(
        json.dumps({
            "command_count": 1,
            "summaries": [{"command_class": "direnv_activation", "command_count": 1}],
            "windows": [{
                "command_class": "direnv_activation",
                "command": ["direnv", "allow"],
                "duration_seconds": 2.0,
                "machine_pressure_state": "io_pressure",
            }],
            "caveats": [],
        }),
        encoding="utf-8",
    )

    config = type("Config", (), {"analysis_output_dir": analysis_root, "local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.core.freshness.get_config", lambda: config)

    from lynchpin.mcp.tools.machine import (
        machine_below_attributions,
        machine_below_analysis,
        machine_command_performance,
        machine_devshell_performance,
        machine_calibration_fixtures,
        machine_measurement_system,
        machine_dataset_diagnostics,
        machine_dataset_inventory,
        machine_materialization_health,
        machine_context_windows,
        machine_episodes,
        machine_telemetry_analysis,
        machine_observational_deltas,
        machine_experiment_claims,
        machine_benchmark_runs,
        machine_benchmark_phases,
        machine_benchmark_estimates,
        machine_feature_frames,
        machine_comparisons,
        machine_attribution_candidates,
        machine_observation_cohorts,
        machine_discovery_validation_splits,
        machine_boundary_candidates,
        machine_benchmark_plans,
        machine_benchmark_plan_template,
        machine_benchmark_manifest_bundle,
        machine_benchmark_execution_handoff,
        machine_below_export_handoff,
        machine_experiment_manifest_diagnostics,
        machine_benchmark_readiness,
        machine_derivation_inventory,
        machine_support_assessments,
        machine_attribution_candidate_details,
        machine_mechanism_hypotheses,
        machine_instrumentation_gaps,
        machine_attribution_claims,
        machine_claim_evidence,
        machine_assumption_checks,
        machine_status,
        machine_validation_design,
        machine_matched_designs,
        machine_negative_controls,
        machine_lagged_exposures,
        machine_anomaly_clusters,
        machine_mining_scans,
        machine_observational_baselines,
        machine_work_observation_artifact,
        machine_work_state_windows,
    )

    assert len(machine_episodes(start="2026-05-01", kind="load_pressure")) == 1
    inventory = machine_dataset_inventory()
    assert inventory["summary"]["table_count"] == 1
    health = machine_materialization_health()
    assert health["summary"]["status"] == "degraded"
    assert health["latest_materialization_report"]["step_count"] == 1
    calibration = machine_calibration_fixtures(kind="null", status="passed")
    assert calibration["fixtures"][0]["fixture_id"] == "fixture1"
    measurement = machine_measurement_system(kind="timer_resolution_clock_source", status="passed")
    assert measurement["checks"][0]["check_id"] == "measure1"
    work_observations = machine_work_observation_artifact()
    assert work_observations["summary"]["failure_summary_count"] == 1
    assert work_observations["failure_summaries"][0]["failure_kind"] == "test"
    dataset_diag = machine_dataset_diagnostics(kind="feature_frame_coverage", severity="info")
    assert dataset_diag["summary"]["feature_status"] == "ready_for_mining"
    assert dataset_diag["diagnostics"][0]["diagnostic_id"] == "diag1"
    assert len(machine_context_windows(project="sinity-lynchpin", has_episodes=True)) == 1
    result = machine_below_attributions(episode_kind="load_pressure")
    assert result["summary"]["attributed_episode_count"] == 1
    assert result["attributions"][0]["capture_id"] == "cap1"
    workload_result = machine_below_attributions(episode_kind="load_pressure", attribution_source="workload_resource")
    assert workload_result["summary"]["workload_resource_attributed_pressure_episode_count"] == 1
    assert workload_result["attributions"][0]["work_source_id"] == "xtask:1"
    telemetry = machine_telemetry_analysis(section="signals")
    assert telemetry["summary"]["coverage"]["sample_count"] == 10
    assert telemetry["rows"][0]["metric"] == "avg_load_1m"
    below_analysis = machine_below_analysis(section="processes", capture_id="cap1")
    assert below_analysis["summary"]["live_store"]["index_count"] == 2
    assert below_analysis["rows"][0]["key"] == "pytest"
    state_windows = machine_work_state_windows(pressure_state="io_pressure", work_state="test_workload", project="sinex")
    assert state_windows["summary"]["filtered_count"] == 1
    assert state_windows["windows"][0]["window_id"] == "state1"
    command_perf = machine_command_performance(tool="pytest", project="sinex", pressure_only=True)
    assert command_perf["summary"]["tool_count"] == 1
    assert command_perf["windows"][0]["duration_seconds"] == 12.0
    deltas = machine_observational_deltas(tool="pytest", pressure_state="io_pressure")
    assert deltas["summary"]["filtered_delta_count"] == 1
    assert deltas["deltas"][0]["median_delta_seconds"] == 3.0
    devshell = machine_devshell_performance(command_class="direnv_activation", pressure_only=True)
    assert devshell["summary"]["summary_count"] == 1
    assert devshell["windows"][0]["duration_seconds"] == 2.0
    baselines = machine_observational_baselines(dimension="hour", key="12")
    assert baselines["summary"]["family_counts"]["hour"] == 1
    assert baselines["rows"][0]["sample_count"] == 10
    claims = machine_experiment_claims(claim_mode="manifest_observational")
    assert claims["summary"]["observational_claim_count"] == 1
    assert claims["summary"]["by_manifest_validation_status"] == {"invalid": 1}
    assert claims["claim_packs"][0]["run_id"] == "run1"
    runs = machine_benchmark_runs(run_group_id="grp1", workload="xtask")
    assert runs["summary"]["by_manifest_validation_status"] == {"invalid": 1}
    assert runs["runs"][0]["run_id"] == "run1"
    phases = machine_benchmark_phases(run_id="run1", phase="build")
    assert phases["phases"][0]["activity_id"] == "a1"
    estimates = machine_benchmark_estimates(run_group_id="grp1", metric="duration_seconds")
    assert estimates["estimates"][0]["delta"] == 1.0
    frames = machine_feature_frames()
    assert frames["summary"]["frame_id"] == "frame1"
    assert frames["summary"]["leakage_status"] == "ok"
    mining = machine_mining_scans()
    assert mining["scan"]["scan_id"] == "scan1"
    assert mining["summary"]["cohort_count"] == 1
    assert mining["summary"]["lagged_exposure_count"] == 1
    assert mining["summary"]["anomaly_cluster_count"] == 1
    lagged = machine_lagged_exposures(project="sinex", pressure_metric="host_io_pressure_some_avg10_max")
    assert lagged["lagged_exposures"][0]["summary_id"] == "lag1"
    clusters = machine_anomaly_clusters(project="sinex")
    assert clusters["anomaly_clusters"][0]["cluster_id"] == "cluster1"
    cohorts = machine_observation_cohorts(dimension="stage_name", project="sinex")
    assert cohorts["cohorts"][0]["cohort_id"] == "cohort1"
    validation = machine_validation_design()
    assert validation["summary"]["boundary_count"] == 1
    assert validation["boundaries"][0]["boundary_id"] == "boundary1"
    splits = machine_discovery_validation_splits(project="sinex")
    assert splits["split"]["split_id"] == "split1"
    boundaries = machine_boundary_candidates(boundary_type="git_commit_transition", project="sinex")
    assert boundaries["boundaries"][0]["boundary_id"] == "boundary1"
    matched = machine_matched_designs(status="design_ready")
    assert matched["summary"]["design_count"] == 1
    assert matched["designs"][0]["design_id"] == "design1"
    from lynchpin.mcp.tools.machine import machine_matched_comparisons
    matched_comparisons = machine_matched_comparisons(candidate_id="cand1", boundary_id="boundary1")
    assert matched_comparisons["comparisons"][0]["design_id"] == "design1"
    negative = machine_negative_controls(status="passed")
    assert negative["summary"]["control_count"] == 1
    assert negative["controls"][0]["control_id"] == "neg1"
    comparisons = machine_comparisons(signal="screening_signal")
    assert comparisons["summary"]["contrast_count"] == 1
    assert comparisons["contrasts"][0]["contrast_id"] == "contrast1"
    candidates = machine_attribution_candidates(
        validation_status="design_ready",
        mechanism_family="stage_regression_or_workload_mix",
        pareto_frontier=True,
    )
    assert candidates["summary"]["pareto_frontier_count"] == 1
    assert candidates["summary"]["by_validation_status"] == {"design_ready": 1}
    assert candidates["candidates"][0]["candidate_id"] == "cand1"
    plans = machine_benchmark_plans(status="needs_binding", run_group_id="grp1", candidate_id="cand1")
    assert plans["summary"]["plan_count"] == 1
    assert plans["plans"][0]["plan_id"] == "plan1"
    template = machine_benchmark_plan_template("cand1")
    assert template["summary"]["planning_status"] == "needs_binding"
    bundle = machine_benchmark_manifest_bundle()
    assert bundle["summary"]["run_template_count"] == 2
    assert bundle["groups"][0]["run_group_id"] == "grp1"
    queue = machine_benchmark_execution_handoff(ready_only=True)
    assert queue["summary"]["ready_group_count"] == 1
    assert queue["items"][0]["run_group_id"] == "grp1"
    from lynchpin.mcp.tools.machine import machine_benchmark_selected_runbook
    runbook = machine_benchmark_selected_runbook(run_group_id="grp1")
    assert runbook["summary"]["status"] == "ready"
    assert "--execute --materialize-after" in runbook["commands"][0]
    below_handoff = machine_below_export_handoff(kind="load_pressure")
    assert below_handoff["summary"]["planned_window_count"] == 1
    assert below_handoff["summary"]["failed_capture_count"] == 1
    assert below_handoff["items"][0]["capture_id"] == "pressure-load-1"
    assert below_handoff["failed_captures"][0]["capture_id"] == "pressure-empty-1"
    manifest_diag = machine_experiment_manifest_diagnostics(kind="executed_run", controlled_valid=True)
    assert manifest_diag["summary"]["controlled_benchmark_valid_count"] == 1
    assert manifest_diag["diagnostics"][0]["relative_path"] == "grp1/runs/run1/manifest.json"
    readiness = machine_benchmark_readiness(payload_json=json.dumps({
        "controlled_benchmark": {
            "run_group_id": "grp1",
            "derivations": [{"drv_path": "/nix/store/demo.drv"}],
            "cache_conditions": ["cold", "warm"],
            "assignment_seed": 1,
            "randomized_order": [
                {"run_id": "r1", "treatment_label": "baseline", "cache_condition": "cold"},
                {"run_id": "r2", "treatment_label": "turbo", "cache_condition": "cold"},
                {"run_id": "r3", "treatment_label": "baseline", "cache_condition": "warm"},
                {"run_id": "r4", "treatment_label": "turbo", "cache_condition": "warm"},
            ],
            "control_label": "baseline",
            "treatment_label": "turbo",
            "internal_json": {
                "path": "/tmp/run.ndjson",
                "log_format": "internal-json",
                "capture_stream": "stderr",
                "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
            },
            "telemetry": {"window_source": "manifest_timestamps"},
        },
        "pre_analysis": {
            "research_question": "Does turbo change duration?",
            "hypothesis": "turbo affects duration",
            "estimand": "mean delta",
            "unit": "run",
            "primary_metric": "duration_seconds",
            "inclusion_rules": ["successful command exit"],
            "exclusion_rules": ["missing internal-json"],
            "blocking_keys": ["cache_condition", "derivation"],
            "support_ceiling": "controlled",
            "causal_model": {"treatment_variable": "turbo", "outcome_variable": "duration_seconds"},
            "instrumentation_bundle": {"name": "build_phase"},
            "power_note": {"status": "fixture"},
        },
    }))
    assert readiness["readiness"]["controlled"] is True
    derivations = machine_derivation_inventory(project="sinex")
    assert derivations["summary"]["ready_target_count"] == 1
    assert derivations["targets"][0]["attr"] == "xtask"
    assessments = machine_support_assessments(support_level="insufficient")
    assert assessments["summary"]["refusal_count"] == 1
    assert assessments["summary"]["by_support_level"] == {"insufficient": 1}
    assert assessments["assessments"][0]["assessment_id"] == "assess1"
    details = machine_attribution_candidate_details("cand1")
    assert details["summary"]["status"] == "found"
    assert details["candidate"]["candidate_id"] == "cand1"
    assert details["summary"]["run_group_ids"] == ["grp1"]
    assert details["manifest_groups"][0]["run_group_id"] == "grp1"
    assert details["preflight_runs"][0]["run_id"] == "template1"
    assert details["support_assessments"][0]["assessment_id"] == "assess1"
    assert details["instrumentation_gaps"][0]["gap_id"] == "gap1"
    assert details["attribution_claims"][0]["claim_id"] == "claim1"
    mechanisms = machine_mechanism_hypotheses(family="stage_regression_or_workload_mix", candidate_id="cand1")
    assert mechanisms["summary"]["mechanism_count"] == 1
    assert mechanisms["mechanisms"][0]["mechanism_id"] == "machine-mechanism:stage_regression_or_workload_mix"
    gaps = machine_instrumentation_gaps(project="sinex", source="controlled_benchmark_run")
    assert gaps["summary"]["gap_count"] == 1
    assert gaps["gaps"][0]["gap_id"] == "gap1"
    attribution_claims = machine_attribution_claims(
        support_level="insufficient",
        project="sinex",
        metric="stage.duration_s",
    )
    assert attribution_claims["summary"]["claim_count"] == 2
    assert attribution_claims["summary"]["filters"] == {
        "support_level": "insufficient",
        "project": "sinex",
        "metric": "stage.duration_s",
    }
    assert len(attribution_claims["claims"]) == 1
    assert attribution_claims["claims"][0]["claim_id"] == "claim1"
    claim_evidence = machine_claim_evidence("claim2")
    assert claim_evidence["source_ids"] == ["assess2", "cand1", "design1"]
    assert claim_evidence["claim"]["claim_id"] == "claim2"
    assert claim_evidence["support_assessments"][0]["assessment_id"] == "assess1"
    assert claim_evidence["support_assessments"][1]["assessment_id"] == "assess2"
    assert claim_evidence["matched_designs"][0]["design_id"] == "design1"
    assert claim_evidence["negative_controls"][0]["control_id"] == "neg1"
    assumptions = machine_assumption_checks(status="failed")
    assert assumptions["summary"]["check_count"] == 2
    assert len(assumptions["checks"]) == 1
    assert assumptions["checks"][0]["assumption_id"] == "a1"
    status = machine_status()
    assert status["artifacts"]["available"] == 13
    assert status["support"]["natural_experiment"] == 1
    assert status["experiment_manifests"]["legacy_observational_count"] == 1
    assert status["claims"]["by_support_level"] == {"insufficient": 1, "natural_experiment": 1}
    assert status["measurement"]["by_status"] == {"passed": 1}
    assert status["assumptions"]["by_status"] == {"failed": 1, "passed": 1}
    assert status["assumptions"]["failed_by_support_level"] == {"unknown": 1}
    assert "readiness dimension controlled_benchmark_claims is missing" in status["blockers"]
    assert "1 unclassified attribution assumption checks failed" in status["blockers"]


def test_machine_list_tools_fail_when_required_artifact_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    config = type("Config", (), {"analysis_output_dir": analysis_root, "local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.core.freshness.get_config", lambda: config)

    from lynchpin.mcp.tools.machine import machine_context_windows, machine_episodes

    with pytest.raises(FileNotFoundError, match="machine_episode_analysis.json"):
        machine_episodes()
    with pytest.raises(FileNotFoundError, match="machine_context_windows.json"):
        machine_context_windows()


def test_machine_episodes_rejects_stale_detector_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    (analysis_root / "machine_episode_analysis.json").write_text(
        json.dumps({"episodes": []}),
        encoding="utf-8",
    )
    config = type("Config", (), {"analysis_output_dir": analysis_root, "local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.core.freshness.get_config", lambda: config)

    from lynchpin.mcp.tools.machine import machine_episodes

    from lynchpin.core.errors import SchemaVersionError
    with pytest.raises(SchemaVersionError, match="machine_episode_analysis"):
        machine_episodes()
