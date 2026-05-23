from __future__ import annotations

import json
from datetime import datetime, timezone

from lynchpin.analysis.machine.readiness import analyze_machine_analysis_readiness
from lynchpin.substrate.connection import apply_schema, connect


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
        "machine_experiment_claims.json": {"run_count": 1, "controlled_claim_count": 1},
    }.items():
        (artifact_root / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("lynchpin.analysis.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    assert dimensions["continuous_machine_telemetry"].status == "stable"
    assert dimensions["window_pre_post_bios_comparison"].status == "limited"
    assert dimensions["all_data_pre_post_bios_comparison"].status == "limited"
    assert dimensions["network_telemetry"].status == "limited"
    assert "too few network probe rows for robust network-path analysis" in dimensions["network_telemetry"].caveats
    assert dimensions["below_process_attribution"].status == "stable"
    assert dimensions["controlled_benchmark_claims"].status == "stable"
    assert any(row.table == "machine_metric_sample" and row.row_count == 120 for row in analysis.tables)
    assert any(row.artifact == "devshell_performance.json" and row.primary_count == 1 for row in analysis.artifacts)


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
    monkeypatch.setattr("lynchpin.analysis.core.io.resolve_analysis_path", lambda name: artifact_root / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    below = dimensions["below_process_attribution"]
    assert below.status == "limited"
    assert "attributed_pressure_episodes=1/10" in below.evidence
    assert "most pressure episodes lack bounded below process/cgroup attribution" in below.caveats


def test_machine_readiness_marks_missing_controlled_claims(monkeypatch, tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    monkeypatch.setattr("lynchpin.analysis.core.io.resolve_analysis_path", lambda name: tmp_path / name)

    analysis = analyze_machine_analysis_readiness(path=db)

    dimensions = {row.dimension: row for row in analysis.dimensions}
    assert dimensions["continuous_machine_telemetry"].status == "missing"
    assert dimensions["controlled_benchmark_claims"].status == "missing"
    assert "benchmark claims require randomized run manifests joined to telemetry by timestamp" in analysis.caveats
