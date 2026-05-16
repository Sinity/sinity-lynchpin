from __future__ import annotations

import json
from datetime import datetime, timezone

from lynchpin.analysis.machine.experiments import analyze_machine_experiment_claims
from lynchpin.substrate.connection import apply_schema, connect


def test_machine_experiment_claims_refuse_controlled_language_without_randomization(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, cwd, started_at, ended_at,
                exit_status, planned_treatment, git_root, git_head, git_branch,
                git_dirty, pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                'run1', 'host', 'xtask', ['just','check'], '/realm/project/sinex',
                ?, ?, 0, ?, '/realm/project/sinex', 'abc', 'master',
                true, '{}', '{}', [], '/tmp/run1/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 2, tzinfo=timezone.utc),
                json.dumps({"turbo": "observed"}),
            ],
        )
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, mem_avail_mb, io_psi_full_avg10,
                gpu_pcie_gen, gpu_pcie_width, gap_codes, refresh_id
            ) VALUES
                (?, 'host', 'machine.telemetry', 2, 21, 32000, 0.2, 4, 16, [], 'r1'),
                (?, 'host', 'machine.telemetry', 2, 22, 31900, 0.3, 4, 16, [], 'r1')
            """,
            [
                datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, 30, tzinfo=timezone.utc),
            ],
        )

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1")

    assert analysis.run_count == 1
    assert analysis.controlled_claim_count == 0
    assert analysis.observational_claim_count == 1
    assert "controlled benchmark claims are refused" in analysis.caveats[0]
    pack = analysis.claim_packs[0]
    assert pack.claim_mode == "manifest_observational"
    assert pack.treatment_label == "turbo=observed"
    assert pack.telemetry.sample_count == 2
    assert pack.telemetry.gpu_pcie_regimes == ("gen4x16",)
    assert any("observational manifest only" in caveat for caveat in pack.caveats)
    assert any("git checkout was dirty" in caveat for caveat in pack.caveats)


def test_machine_experiment_claims_allow_controlled_mode_only_with_manifest_structure(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, started_at, ended_at,
                planned_treatment, pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                'run2', 'host', 'xtask', ['just','check'], ?, ?,
                ?, '{}', '{}', [], '/tmp/run2/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                json.dumps({"assignment_seed": "s1", "control_label": "baseline", "treatment_label": "turbo"}),
            ],
        )

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1")

    assert analysis.controlled_claim_count == 1
    assert analysis.claim_packs[0].claim_mode == "controlled_benchmark"
    assert not any("observational manifest only" in caveat for caveat in analysis.claim_packs[0].caveats)


def test_machine_experiment_claims_accept_plain_randomized_flag(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, started_at, ended_at,
                planned_treatment, pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                'run3', 'host', 'xtask', ['just','check'], ?, ?,
                ?, '{}', '{}', [], '/tmp/run3/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                json.dumps({"randomized": True, "control_label": "baseline", "treatment_label": "turbo"}),
            ],
        )

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1")

    assert analysis.controlled_claim_count == 1
    assert analysis.claim_packs[0].claim_mode == "controlled_benchmark"


def test_machine_experiment_claims_use_inspection_window_for_zero_duration(tmp_path):
    db = tmp_path / "sub.duckdb"
    started = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, started_at, ended_at,
                planned_treatment, pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                'run4', 'host', 'smoke', ['true'], ?, ?,
                ?, '{}', '{}', [], '/tmp/run4/manifest.json', 'r1'
            )
            """,
            [started, started, json.dumps({"turbo": "observed"})],
        )
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, source_schema_version,
                load_1m, mem_avail_mb, gap_codes, refresh_id
            ) VALUES (?, 'host', 'machine.telemetry', 2, 1.0, 32000, [], 'r1')
            """,
            [datetime(2026, 5, 1, 12, 3, tzinfo=timezone.utc)],
        )

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1")

    pack = analysis.claim_packs[0]
    assert pack.duration_seconds is None
    assert pack.telemetry.sample_count == 1
    assert any("no positive duration" in caveat for caveat in pack.caveats)
