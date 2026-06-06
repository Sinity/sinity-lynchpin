from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lynchpin.analysis.machine.readiness import analyze_machine_analysis_readiness
from lynchpin.substrate.connection import apply_schema, connect


@pytest.fixture(autouse=True)
def _isolate_freshness_ledger(monkeypatch, tmp_path):
    config = type("Config", (), {"local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.freshness.get_config", lambda: config)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, cfg: type(
            "Result",
            (),
            {"to_json": lambda self: {"status": "ready", "changed": False, "reason": "ok"}},
        )(),
    )


def test_machine_readiness_reports_source_and_claim_coverage(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for idx in range(120):
            day = 10 if idx < 60 else 13
            conn.execute(
                """
                INSERT INTO machine_metric_sample (
                    observed_at, host, source, source_schema_version,
                    load_1m, mem_avail_mb, gap_codes, refresh_id
                ) VALUES (?, 'host', 'machine.telemetry', 2, 1.0, 32000, [], 'r1')
                """,
                [datetime(2026, 5, day, 12, idx % 60, tzinfo=timezone.utc)],
            )
        conn.execute(
            """
            INSERT INTO machine_network_sample (
                observed_at, host, source_schema_version, interface, gateway_ip,
                ping, iface, nic, tcp, dns_ms, pmtu_1492, conntrack, gap_codes, refresh_id
            ) VALUES (?, 'host', 1, 'enp8s0', '192.0.2.1', '{}', '{}', '{}', '{}', 4, true, '{}', [], 'r1')
            """,
            [datetime(2026, 5, 13, 12, tzinfo=timezone.utc)],
        )
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, started_at, planned_treatment,
                pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                'run-1', 'host', 'nix-develop', ['nix','develop'], ?,
                '{"randomized":true,"control_label":"a","treatment_label":"b"}',
                '{}', '{}', [], '/tmp/run.json', 'r1'
            )
            """,
            [datetime(2026, 5, 13, 12, tzinfo=timezone.utc)],
        )

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    for name, payload in {
        "command_performance_windows.json": {"command_count": 3},
        "devshell_performance.json": {"command_count": 1},
        "machine_below_attribution.json": {"attributed_episode_count": 2, "pressure_episode_count": 2},
        "machine_experiment_claims.json": {
            "run_count": 1,
            "controlled_claim_count": 1,
            "claim_packs": [{
                "claim_mode": "controlled_benchmark",
                "internal_json": {"exists": True, "phase_count": 2},
            }],
        },
        "machine_benchmark_preflight.json": {
            "run_count": 2,
            "ready_run_count": 2,
            "issue_count": 0,
            "warning_count": 1,
        },
        "machine_support_assessment.json": {
            "assessment_count": 2,
            "refusal_count": 0,
            "controlled_claim_count": 1,
            "natural_experiment_support_count": 1,
            "ready_plan_count": 1,
            "assessments": [{
                "support_level": "natural_experiment",
                "source_ids": ["machine-matched-design:selected"],
            }],
        },
        "machine_matched_designs.json": {"design_count": 2},
        "machine_negative_controls.json": {
            "control_count": 3,
            "by_status": {"failed": 1, "passed": 2},
            "controls": [
                {"design_id": "machine-matched-design:selected", "status": "passed"},
                {"design_id": "machine-matched-design:selected", "status": "passed"},
                {"design_id": "machine-matched-design:unselected", "status": "failed"},
            ],
        },
        "machine_measurement_system.json": {
            "check_count": 5,
            "by_status": {"passed": 4, "untestable": 1},
        },
    }.items():
        (artifact_root / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    assert dimensions["continuous_machine_telemetry"].status == "stable"
    assert dimensions["window_pre_post_bios_comparison"].status == "limited"
    assert dimensions["all_data_pre_post_bios_comparison"].status == "limited"
    assert dimensions["network_telemetry"].status == "limited"
    assert "too few network probe rows for robust network-path analysis" in dimensions["network_telemetry"].caveats
    assert dimensions["below_process_attribution"].status == "stable"
    assert dimensions["controlled_benchmark_claims"].status == "stable"
    assert dimensions["devshell_nix_focus"].status == "stable"
    assert "structured_internal_json_run_count=1" in dimensions["devshell_nix_focus"].evidence
    assert dimensions["controlled_benchmark_exportability"].status == "stable"
    assert "run templates still carry export-time warnings" in dimensions["controlled_benchmark_exportability"].caveats
    assert dimensions["causal_support_gate"].status == "stable"
    assert dimensions["natural_experiment_identification"].status == "stable"
    assert "support_selected_designs=1" in dimensions["natural_experiment_identification"].evidence
    assert "support_selected_negative_controls_failed=0" in dimensions["natural_experiment_identification"].evidence
    assert "some non-selected negative controls failed" in dimensions["natural_experiment_identification"].caveats
    assert dimensions["measurement_system_diagnostics"].status == "stable"
    assert any(row.table == "machine_metric_sample" and row.row_count == 120 for row in analysis.tables)
    metric_table = next(row for row in analysis.tables if row.table == "machine_metric_sample")
    assert metric_table.materialized_snapshot_count == 1
    assert metric_table.latest_materialized_refresh_id == "r1"
    assert "1 materialized snapshots" in dimensions["continuous_machine_telemetry"].evidence
    assert any(row.artifact == "devshell_performance.json" and row.primary_count == 1 for row in analysis.artifacts)


def test_machine_readiness_labels_window_and_all_data_pre_post_caveats(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, mem_avail_mb, gap_codes, refresh_id
            ) VALUES (?, 'host', 'machine.telemetry', 2, 1.0, 32000, [], 'r1')
            """,
            [datetime(2026, 5, 13, 12, tzinfo=timezone.utc)],
        )
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: tmp_path / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    assert "no pre-boundary machine_metric_sample rows in current analysis window" in (
        dimensions["window_pre_post_bios_comparison"].caveats
    )
    assert "no pre-boundary machine_metric_sample rows in promoted machine substrate" in (
        dimensions["all_data_pre_post_bios_comparison"].caveats
    )


def test_machine_readiness_limits_sparse_below_attribution(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    (artifact_root / "machine_below_attribution.json").write_text(
        json.dumps({"attributed_episode_count": 1, "pressure_episode_count": 10}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    below = dimensions["below_process_attribution"]
    assert below.status == "limited"
    assert "bounded_below_capture_count=0" in below.evidence
    assert "live_below_store_indexes=0" in below.evidence
    assert "bounded_below_attributed_pressure_episodes=1/10" in below.evidence
    assert "combined_attributed_pressure_episodes=1/10" in below.evidence
    assert "most pressure episodes lack bounded below or workload resource attribution" in below.caveats
    assert "no bounded below captures are available for process/cgroup attribution" in below.caveats


def test_machine_readiness_counts_workload_resource_attribution(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    (artifact_root / "machine_below_attribution.json").write_text(
        json.dumps({
            "attributed_episode_count": 0,
            "pressure_episode_count": 10,
            "workload_resource_attributed_pressure_episode_count": 8,
            "residual_unattributed_pressure_episode_count": 2,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    below = {row.dimension: row for row in analysis.dimensions}["below_process_attribution"]
    assert below.status == "stable"
    assert "bounded_below_attributed_pressure_episodes=0/10" in below.evidence
    assert "workload_resource_attributed_pressure_episodes=8/10" in below.evidence
    assert "combined_attributed_pressure_episodes=8/10" in below.evidence


def test_machine_readiness_explains_nonoverlapping_below_capture(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    (artifact_root / "machine_below_attribution.json").write_text(
        json.dumps({
            "attributed_episode_count": 0,
            "capture_count": 1,
            "pressure_episode_count": 10,
            "workload_resource_attributed_pressure_episode_count": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    below = {row.dimension: row for row in analysis.dimensions}["below_process_attribution"]
    assert below.status == "limited"
    assert "bounded_below_capture_count=1" in below.evidence
    assert "bounded below captures do not overlap current machine pressure episodes" in below.caveats


def test_machine_readiness_surfaces_live_below_store_without_exports(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    (artifact_root / "machine_below_attribution.json").write_text(
        json.dumps({
            "attributed_episode_count": 0,
            "capture_count": 0,
            "live_store_index_count": 4,
            "live_store_first_observed_at": "2026-05-01T00:00:00+00:00",
            "live_store_last_observed_at": "2026-05-05T00:00:00+00:00",
            "pressure_episode_count": 10,
            "workload_resource_attributed_pressure_episode_count": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    below = {row.dimension: row for row in analysis.dimensions}["below_process_attribution"]
    assert below.status == "limited"
    assert "live_below_store_indexes=4" in below.evidence
    assert "live_below_store_span=2026-05-01T00:00:00+00:00..2026-05-05T00:00:00+00:00" in below.evidence
    assert "live below store exists but bounded exports or decoder output are missing for pressure episodes" in (
        below.caveats
    )


def test_machine_readiness_marks_missing_controlled_claims(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: tmp_path / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    assert dimensions["continuous_machine_telemetry"].status == "missing"
    assert dimensions["controlled_benchmark_claims"].status == "missing"
    assert dimensions["controlled_benchmark_exportability"].status == "missing"
    assert dimensions["causal_support_gate"].status == "missing"
    assert dimensions["natural_experiment_identification"].status == "missing"
    assert dimensions["measurement_system_diagnostics"].status == "missing"
    assert "benchmark claims require randomized run manifests joined to telemetry by timestamp" in analysis.caveats


def test_machine_readiness_surfaces_failed_support_infra(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    for name, payload in {
        "machine_benchmark_preflight.json": {
            "run_count": 3,
            "ready_run_count": 1,
            "issue_count": 2,
            "warning_count": 0,
        },
        "machine_support_assessment.json": {
            "assessment_count": 3,
            "refusal_count": 3,
            "controlled_claim_count": 0,
            "natural_experiment_support_count": 0,
            "ready_plan_count": 1,
        },
        "machine_matched_designs.json": {"design_count": 1},
        "machine_negative_controls.json": {
            "control_count": 2,
            "by_status": {"failed": 1, "passed": 1},
        },
        "machine_measurement_system.json": {
            "check_count": 5,
            "by_status": {"failed": 1, "missing": 1, "passed": 3},
        },
    }.items():
        (artifact_root / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    assert dimensions["controlled_benchmark_exportability"].status == "limited"
    assert "one or more run templates fail benchmark preflight" in dimensions["controlled_benchmark_exportability"].caveats
    assert dimensions["causal_support_gate"].status == "limited"
    assert "no candidate currently passes the controlled or natural-experiment support gate" in dimensions["causal_support_gate"].caveats
    assert dimensions["natural_experiment_identification"].status == "limited"
    assert "no natural-experiment support-selected designs are available" in dimensions["natural_experiment_identification"].caveats
    assert "some non-selected negative controls failed" in dimensions["natural_experiment_identification"].caveats
    assert dimensions["measurement_system_diagnostics"].status == "limited"
    assert "one or more measurement-system diagnostics failed" in dimensions["measurement_system_diagnostics"].caveats


def test_machine_readiness_limits_natural_support_when_selected_controls_fail(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    for name, payload in {
        "machine_support_assessment.json": {
            "assessments": [{
                "support_level": "natural_experiment",
                "source_ids": ["machine-matched-design:selected"],
            }]
        },
        "machine_matched_designs.json": {"design_count": 1},
        "machine_negative_controls.json": {
            "control_count": 2,
            "by_status": {"failed": 1, "passed": 1},
            "controls": [
                {"design_id": "machine-matched-design:selected", "status": "failed"},
                {"design_id": "machine-matched-design:selected", "status": "passed"},
            ],
        },
    }.items():
        (artifact_root / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    natural = {row.dimension: row for row in analysis.dimensions}["natural_experiment_identification"]
    assert natural.status == "limited"
    assert "support_selected_negative_controls_failed=1" in natural.evidence
    assert "one or more support-selected negative controls failed" in natural.caveats


def test_machine_readiness_limits_devshell_when_only_command_text_exists(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    artifact_root = tmp_path / "analysis"
    artifact_root.mkdir()
    for name, payload in {
        "devshell_performance.json": {"command_count": 2},
        "machine_experiment_claims.json": {
            "controlled_claim_count": 0,
            "claim_packs": [{
                "claim_mode": "manifest_observational",
                "internal_json": {"exists": False, "phase_count": 0},
            }],
        },
    }.items():
        (artifact_root / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    devshell = {row.dimension: row for row in analysis.dimensions}["devshell_nix_focus"]
    assert devshell.status == "limited"
    assert "devshell_command_count=2" in devshell.evidence
    assert "structured_internal_json_run_count=0" in devshell.evidence
    assert "no parsed Nix internal-json benchmark phases are available" in devshell.caveats
