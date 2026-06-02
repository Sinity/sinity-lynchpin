from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from lynchpin.analysis.machine.experiments import analyze_machine_experiment_claims
from lynchpin.substrate.connection import apply_schema, connect


def _controlled_planned(
    label: str,
    *,
    run_id: str,
    seed: int = 42,
    internal_json: str = "/tmp/run/internal.json",
    include_top_level_labels: bool = True,
) -> str:
    schedule = [
        {
            "run_id": "run-control",
            "treatment_label": "baseline",
            "cache_condition": "cold",
            "derivation_key": "/nix/store/demo.drv",
        },
        {
            "run_id": "run-treatment",
            "treatment_label": "turbo",
            "cache_condition": "warm",
            "derivation_key": "/nix/store/demo.drv",
        },
        {
            "run_id": "run-control-warm",
            "treatment_label": "baseline",
            "cache_condition": "warm",
            "derivation_key": "/nix/store/demo.drv",
        },
        {
            "run_id": "run-treatment-cold",
            "treatment_label": "turbo",
            "cache_condition": "cold",
            "derivation_key": "/nix/store/demo.drv",
        },
    ]
    selected = next(row for row in schedule if row["run_id"] == run_id)
    payload = {
        "controlled_benchmark": {
            "run_group_id": "grp-1",
            "derivations": [{"name": "sinex-check", "drv_path": "/nix/store/demo.drv"}],
            "cache_conditions": ["warm", "cold"],
            "assignment_seed": seed,
            "randomized_order": schedule,
            "control_label": "baseline",
            "treatment_label": "turbo",
            "internal_json": {
                "path": internal_json,
                "log_format": "internal-json",
                "capture_stream": "stderr",
                "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
            },
            "telemetry": {"window_source": "manifest_timestamps"},
        },
        "selected_run": {
            **selected,
            "sequence_index": schedule.index(selected) + 1,
            "telemetry_window_id": f"grp-1:{run_id}:manifest_timestamps",
            "internal_json_path": internal_json,
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
    }
    if include_top_level_labels:
        payload["treatment_label"] = label
        payload["cache_condition"] = selected["cache_condition"]
    return json.dumps(payload)


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

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

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
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text(
        "\n".join([
            '{"action":"start","id":1,"timestamp":"2026-05-01T12:00:00+00:00"}',
            '{"action":"stop","id":1,"timestamp":"2026-05-01T12:00:01+00:00"}',
        ]),
        encoding="utf-8",
    )
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, run_group_id, host, workload, command, started_at, ended_at,
                monotonic_started_ns, monotonic_ended_ns, execution_outcome,
                measurement_context, nix_internal_json_path,
                planned_treatment, pre_state, post_state, notes,
                validation_status, manifest_validation, manifest_path, refresh_id
            ) VALUES (
                'run-treatment', 'grp-1', 'host', 'xtask', ['just','check'], ?, ?,
                100, 200, '{"status":"success","censored":false}',
                '{"host_boot_id":"boot1"}', ?,
                ?, '{}', '{}', [],
                'valid', '{"valid":true}', '/tmp/run-treatment/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                str(internal_json),
                _controlled_planned("turbo", run_id="run-treatment", internal_json=str(internal_json)),
            ],
        )
        _insert_metric(conn, datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 1
    assert analysis.claim_packs[0].claim_mode == "controlled_benchmark"
    assert not any("observational manifest only" in caveat for caveat in analysis.claim_packs[0].caveats)
    assert analysis.claim_packs[0].run_group_id == "grp-1"
    assert analysis.claim_packs[0].benchmark_readiness["derivation_count"] == 1
    assert analysis.claim_packs[0].internal_json["parsed_count"] == 2
    assert analysis.claim_packs[0].internal_json["phases"][0]["status"] == "complete"
    assert analysis.claim_packs[0].monotonic_started_ns == 100
    assert analysis.claim_packs[0].monotonic_ended_ns == 200
    assert analysis.claim_packs[0].execution_outcome["status"] == "success"
    assert analysis.claim_packs[0].measurement_context["host_boot_id"] == "boot1"
    assert analysis.claim_packs[0].nix_internal_json_path == str(internal_json)


def test_machine_experiment_claims_demote_manifest_validation_issues(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text(
        "\n".join([
            '{"action":"start","id":1,"timestamp":"2026-05-01T12:00:00+00:00"}',
            '{"action":"stop","id":1,"timestamp":"2026-05-01T12:00:01+00:00"}',
        ]),
        encoding="utf-8",
    )
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, run_group_id, host, workload, command, started_at, ended_at,
                monotonic_started_ns, monotonic_ended_ns, execution_outcome,
                measurement_context, nix_internal_json_path,
                planned_treatment, pre_state, post_state, notes,
                validation_status, validation_issues, validation_warnings,
                manifest_validation, manifest_path, refresh_id
            ) VALUES (
                'run-treatment', 'grp-1', 'host', 'xtask', ['just','check'], ?, ?,
                100, 200, '{"status":"success","censored":false}',
                '{"host_boot_id":"boot1"}', ?,
                ?, '{}', '{}', [],
                'invalid', ['missing monotonic timestamp'], [],
                '{"valid":false,"issues":["missing monotonic timestamp"]}',
                '/tmp/run-treatment/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                str(internal_json),
                _controlled_planned("turbo", run_id="run-treatment", internal_json=str(internal_json)),
            ],
        )
        _insert_metric(conn, datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    pack = analysis.claim_packs[0]
    assert analysis.controlled_claim_count == 0
    assert pack.claim_mode == "manifest_observational"
    assert pack.manifest_validation["valid"] is False
    assert any("manifest validation issue: missing monotonic timestamp" in caveat for caveat in pack.caveats)


def test_machine_experiment_claims_tolerate_legacy_schema_without_validation_columns(tmp_path):
    db = tmp_path / "legacy.duckdb"
    with connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE machine_experiment_run (
                run_id VARCHAR, run_group_id VARCHAR, host VARCHAR, workload VARCHAR,
                command VARCHAR[], cwd VARCHAR, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
                monotonic_started_ns BIGINT, monotonic_ended_ns BIGINT,
                exit_status INTEGER, execution_outcome JSON,
                service_profile VARCHAR, cache_profile VARCHAR, measurement_context JSON,
                planned_treatment JSON, nix_internal_json_path VARCHAR,
                git_root VARCHAR, git_head VARCHAR, git_branch VARCHAR, git_dirty BOOLEAN,
                pre_state JSON, post_state JSON, notes VARCHAR[],
                manifest_path VARCHAR, refresh_id VARCHAR, materialized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE machine_metric_sample (
                observed_at TIMESTAMPTZ, host VARCHAR, source VARCHAR,
                load_1m DOUBLE, mem_avail_mb INTEGER, io_psi_full_avg10 DOUBLE,
                io_psi_full_avg60 DOUBLE, gpu_pcie_gen INTEGER, gpu_pcie_width INTEGER,
                refresh_id VARCHAR, materialized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, run_group_id, host, workload, command, cwd, started_at, ended_at,
                monotonic_started_ns, monotonic_ended_ns, exit_status, execution_outcome,
                service_profile, cache_profile, measurement_context, planned_treatment,
                nix_internal_json_path, git_root, git_head, git_branch, git_dirty,
                pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                'legacy-run', 'grp-legacy', 'host', 'xtask', ['just','check'], NULL, ?, ?,
                NULL, NULL, 0, '{"status":"success"}',
                NULL, NULL, '{}', '{"turbo":"observed"}',
                NULL, '/realm/project/sinex', 'abc', 'master', false,
                '{}', '{}', [], '/tmp/legacy/manifest.json', 'legacy-refresh'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 2, tzinfo=timezone.utc),
            ],
        )
        conn.execute(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, source, load_1m, mem_avail_mb, io_psi_full_avg10,
                io_psi_full_avg60, gpu_pcie_gen, gpu_pcie_width, refresh_id
            ) VALUES (
                ?, 'host', 'machine.telemetry', 1.0, 32000, 0.1, 0.1, 4, 16, 'legacy-refresh'
            )
            """,
            [datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc)],
        )

    analysis = analyze_machine_experiment_claims(
        path=db,
        refresh_id="legacy-refresh",
        include_episodes=False,
    )

    pack = analysis.claim_packs[0]
    assert pack.run_id == "legacy-run"
    assert pack.manifest_validation == {"valid": None, "issues": [], "warnings": []}
    assert analysis.controlled_claim_count == 0
    assert pack.claim_mode == "manifest_observational"
    assert any("manifest validation status is unknown" in caveat for caveat in pack.caveats)


def test_machine_experiment_claims_pads_telemetry_for_short_controlled_runs(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text(
        "\n".join([
            '@nix {"action":"start","id":1,"parent":0,"text":"querying info","type":0}',
            '@nix {"action":"stop","id":1}',
        ]),
        encoding="utf-8",
    )
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, run_group_id, host, workload, command, started_at, ended_at,
                monotonic_started_ns, monotonic_ended_ns, execution_outcome,
                measurement_context, nix_internal_json_path,
                planned_treatment, pre_state, post_state, notes,
                validation_status, manifest_validation, manifest_path, refresh_id
            ) VALUES (
                'run-treatment', 'grp-1', 'host', 'xtask', ['just','check'], ?, ?,
                100, 200, '{"status":"success","censored":false}',
                '{"host_boot_id":"boot1"}', ?,
                ?, '{}', '{}', [],
                'valid', '{"valid":true}', '/tmp/run-treatment/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, 10, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 0, 11, tzinfo=timezone.utc),
                str(internal_json),
                _controlled_planned("turbo", run_id="run-treatment", internal_json=str(internal_json)),
            ],
        )
        _insert_metric(conn, datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    pack = analysis.claim_packs[0]
    assert pack.claim_mode == "controlled_benchmark"
    assert pack.telemetry.sample_count == 1
    assert any("cadence padding" in caveat for caveat in pack.caveats)
    assert any("no complete timed phase" in caveat for caveat in pack.caveats)


def test_machine_experiment_claims_demote_executed_run_group_mismatch(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text(
        "\n".join([
            '{"action":"start","id":1,"timestamp":"2026-05-01T12:00:00+00:00"}',
            '{"action":"stop","id":1,"timestamp":"2026-05-01T12:00:01+00:00"}',
        ]),
        encoding="utf-8",
    )
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, run_group_id, host, workload, command, started_at, ended_at,
                planned_treatment, pre_state, post_state, notes,
                validation_status, manifest_validation, manifest_path, refresh_id
            ) VALUES (
                'run-treatment', 'other-group', 'host', 'xtask', ['just','check'], ?, ?,
                ?, '{}', '{}', [],
                'valid', '{"valid":true}', '/tmp/run-treatment/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                _controlled_planned("turbo", run_id="run-treatment", internal_json=str(internal_json)),
            ],
        )
        _insert_metric(conn, datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 0
    pack = analysis.claim_packs[0]
    assert pack.claim_mode == "manifest_observational"
    assert any("executed run_group_id" in caveat for caveat in pack.caveats)


def test_machine_experiment_claims_demote_selected_run_mismatch(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text(
        "\n".join([
            '{"action":"start","id":1,"timestamp":"2026-05-01T12:00:00+00:00"}',
            '{"action":"stop","id":1,"timestamp":"2026-05-01T12:00:01+00:00"}',
        ]),
        encoding="utf-8",
    )
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, started_at, ended_at,
                planned_treatment, pre_state, post_state, notes,
                validation_status, manifest_validation, manifest_path, refresh_id
            ) VALUES (
                'run-control', 'host', 'xtask', ['just','check'], ?, ?,
                ?, '{}', '{}', [],
                'valid', '{"valid":true}', '/tmp/run-control/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                _controlled_planned("turbo", run_id="run-treatment", internal_json=str(internal_json)),
            ],
        )
        _insert_metric(conn, datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 0
    pack = analysis.claim_packs[0]
    assert pack.claim_mode == "manifest_observational"
    assert any("selected-run assignment gap" in caveat for caveat in pack.caveats)
    assert any("does not match executed run_id" in caveat for caveat in pack.caveats)


def test_machine_experiment_claims_reject_plain_randomized_flag(tmp_path):
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

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 0
    assert analysis.claim_packs[0].claim_mode == "manifest_observational"
    assert any("fixed derivation set" in caveat for caveat in analysis.claim_packs[0].caveats)


def test_machine_experiment_claims_emit_bootstrap_estimate_for_run_group(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text(
        "\n".join([
            '{"action":"start","id":1,"timestamp":"2026-05-01T12:00:00+00:00"}',
            '{"action":"stop","id":1,"timestamp":"2026-05-01T12:00:01+00:00"}',
        ]),
        encoding="utf-8",
    )
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, started_at, ended_at,
                planned_treatment, pre_state, post_state, notes,
                validation_status, manifest_validation, manifest_path, refresh_id
            ) VALUES
                (
                    'run-control', 'host', 'xtask', ['just','check'], ?, ?,
                    ?, '{}', '{}', [],
                    'valid', '{"valid":true}', '/tmp/run-control/manifest.json', 'r1'
                ),
                (
                    'run-treatment', 'host', 'xtask', ['just','check'], ?, ?,
                    ?, '{}', '{}', [],
                    'valid', '{"valid":true}', '/tmp/run-treatment/manifest.json', 'r1'
                )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 2, tzinfo=timezone.utc),
                _controlled_planned("baseline", run_id="run-control", internal_json=str(internal_json)),
                datetime(2026, 5, 1, 12, 4, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc),
                _controlled_planned("turbo", run_id="run-treatment", internal_json=str(internal_json)),
            ],
        )
        _insert_metric(conn, datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc))
        _insert_metric(conn, datetime(2026, 5, 1, 12, 4, 30, tzinfo=timezone.utc))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 2
    assert analysis.effect_estimates[0]["run_group_id"] == "grp-1"
    assert analysis.effect_estimates[0]["metric"] == "duration_seconds"
    assert analysis.effect_estimates[0]["estimator"] == "unpaired_bootstrap_mean_delta"
    assert analysis.effect_estimates[0]["delta"] == -60.0
    assert analysis.effect_estimates[0]["p_value_method"] == "exact_label_permutation_two_sided"
    assert 0.0 <= analysis.effect_estimates[0]["p_value"] <= 1.0


def test_machine_experiment_claims_estimate_from_selected_run_labels(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text('@nix {"action":"start","id":1}\n', encoding="utf-8")
    started = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    runs = [
        ("run-control", "baseline", 100),
        ("run-treatment", "turbo", 130),
    ]
    with connect(db) as conn:
        apply_schema(conn)
        for idx, (run_id, label, duration_s) in enumerate(runs):
            run_start = started + timedelta(minutes=idx * 5)
            conn.execute(
                """
                INSERT INTO machine_experiment_run (
                    run_id, host, workload, command, started_at, ended_at,
                    planned_treatment, pre_state, post_state, notes,
                    validation_status, manifest_validation, manifest_path, refresh_id
                ) VALUES (
                    ?, 'host', 'xtask', ['just','check'], ?, ?,
                    ?, '{}', '{}', [],
                    'valid', '{"valid":true}', ?, 'r1'
                )
                """,
                [
                    run_id,
                    run_start,
                    run_start + timedelta(seconds=duration_s),
                    _controlled_planned(
                        label,
                        run_id=run_id,
                        internal_json=str(internal_json),
                        include_top_level_labels=False,
                    ),
                    f"/tmp/{run_id}/manifest.json",
                ],
            )
            _insert_metric(conn, run_start.replace(second=30))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 2
    assert [pack.treatment_label for pack in analysis.claim_packs] == [
        "treatment_label=baseline",
        "treatment_label=turbo",
    ]
    assert [pack.cache_condition for pack in analysis.claim_packs] == ["cold", "warm"]
    assert analysis.effect_estimates[0]["run_group_id"] == "grp-1"
    assert analysis.effect_estimates[0]["delta"] == 30.0


def test_machine_experiment_claims_use_stratified_estimator_for_complete_blocks(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text(
        "\n".join([
            '{"action":"start","id":1,"timestamp":"2026-05-01T12:00:00+00:00"}',
            '{"action":"stop","id":1,"timestamp":"2026-05-01T12:00:01+00:00"}',
        ]),
        encoding="utf-8",
    )
    started = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    runs = [
        ("run-control", "baseline", 100),
        ("run-treatment-cold", "turbo", 110),
        ("run-control-warm", "baseline", 200),
        ("run-treatment", "turbo", 220),
    ]
    with connect(db) as conn:
        apply_schema(conn)
        for idx, (run_id, label, duration_s) in enumerate(runs):
            run_start = started + timedelta(minutes=idx * 5)
            conn.execute(
                """
                INSERT INTO machine_experiment_run (
                    run_id, host, workload, command, started_at, ended_at,
                    planned_treatment, pre_state, post_state, notes,
                    validation_status, manifest_validation, manifest_path, refresh_id
                ) VALUES (
                    ?, 'host', 'xtask', ['just','check'], ?, ?,
                    ?, '{}', '{}', [],
                    'valid', '{"valid":true}', ?, 'r1'
                )
                """,
                [
                    run_id,
                    run_start,
                    run_start + timedelta(seconds=duration_s),
                    _controlled_planned(label, run_id=run_id, internal_json=str(internal_json)),
                    f"/tmp/{run_id}/manifest.json",
                ],
            )
            _insert_metric(conn, run_start.replace(second=30))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 4
    estimate = analysis.effect_estimates[0]
    assert estimate["run_group_id"] == "grp-1"
    assert estimate["estimator"] == "stratified_bootstrap_mean_delta"
    assert estimate["stratum_count"] == 2
    assert estimate["delta"] == 15.0
    assert estimate["p_value_method"] == "exact_stratified_label_permutation_two_sided"
    assert 0.0 <= estimate["p_value"] <= 1.0
    assert estimate["strata"] == (
        "cache=cold|derivation=/nix/store/demo.drv",
        "cache=warm|derivation=/nix/store/demo.drv",
    )


def test_machine_experiment_claims_require_complete_phase_and_telemetry(tmp_path):
    db = tmp_path / "sub.duckdb"
    internal_json = tmp_path / "internal.ndjson"
    internal_json.write_text('{"action":"start","id":1,"timestamp":"2026-05-01T12:00:00+00:00"}\n', encoding="utf-8")
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, started_at, ended_at,
                planned_treatment, pre_state, post_state, notes,
                validation_status, manifest_validation, manifest_path, refresh_id
            ) VALUES (
                'run-treatment', 'host', 'xtask', ['just','check'], ?, ?,
                ?, '{}', '{}', [],
                'valid', '{"valid":true}', '/tmp/run-treatment/manifest.json', 'r1'
            )
            """,
            [
                datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
                _controlled_planned("turbo", run_id="run-treatment", internal_json=str(internal_json)),
            ],
        )
        _insert_metric(conn, datetime(2026, 5, 1, 12, 0, 30, tzinfo=timezone.utc))

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    assert analysis.controlled_claim_count == 1
    pack = analysis.claim_packs[0]
    assert pack.claim_mode == "controlled_benchmark"
    assert any("no complete timed phase" in caveat for caveat in pack.caveats)


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

    analysis = analyze_machine_experiment_claims(path=db, refresh_id="r1", include_episodes=False)

    pack = analysis.claim_packs[0]
    assert pack.duration_seconds is None
    assert pack.telemetry.sample_count == 1
    assert any("no positive duration" in caveat for caveat in pack.caveats)


def _insert_metric(conn, observed_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO machine_metric_sample (
            observed_at, host, source, source_schema_version,
            load_1m, mem_avail_mb, gap_codes, refresh_id
        ) VALUES (?, 'host', 'machine.telemetry', 2, 1.0, 32000, [], 'r1')
        """,
        [observed_at],
    )
