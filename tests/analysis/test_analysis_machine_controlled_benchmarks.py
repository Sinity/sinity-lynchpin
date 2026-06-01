from __future__ import annotations

import json


def test_stratified_bootstrap_delta_ci_uses_complete_blocks_and_reports_drops():
    from lynchpin.analysis.machine.controlled_benchmarks import stratified_bootstrap_delta_ci

    estimate = stratified_bootstrap_delta_ci(
        {
            "cache=cold|derivation=a": (100.0,),
            "cache=warm|derivation=a": (200.0,),
            "cache=missing-treatment|derivation=a": (300.0,),
        },
        {
            "cache=cold|derivation=a": (110.0,),
            "cache=warm|derivation=a": (220.0,),
        },
        metric="duration_seconds",
        control_label="baseline",
        treatment_label="turbo",
        iterations=50,
    )

    assert estimate is not None
    assert estimate.estimator == "stratified_bootstrap_mean_delta"
    assert estimate.stratum_count == 2
    assert estimate.control_n == 2
    assert estimate.treatment_n == 2
    assert estimate.delta == 15.0
    assert estimate.p_value == 0.5
    assert estimate.p_value_method == "exact_stratified_label_permutation_two_sided"
    assert estimate.strata == (
        "cache=cold|derivation=a",
        "cache=warm|derivation=a",
    )
    assert estimate.dropped_strata == ("cache=missing-treatment|derivation=a",)


def test_paired_bootstrap_delta_ci_preserves_pair_unit():
    from lynchpin.analysis.machine.controlled_benchmarks import paired_bootstrap_delta_ci

    estimate = paired_bootstrap_delta_ci(
        ((100.0, 90.0), (200.0, 190.0)),
        metric="duration_seconds",
        control_label="baseline",
        treatment_label="turbo",
        iterations=50,
    )

    assert estimate is not None
    assert estimate.estimator == "paired_bootstrap_mean_delta"
    assert estimate.pair_n == 2
    assert estimate.delta == -10.0
    assert estimate.p_value == 0.5
    assert estimate.p_value_method == "exact_paired_sign_flip_two_sided"


def test_permutation_delta_p_value_is_exact_for_small_exchangeable_samples():
    from lynchpin.analysis.machine.controlled_benchmarks import permutation_delta_p_value

    p_value, method = permutation_delta_p_value((1.0, 2.0), (10.0, 11.0))

    assert p_value == 0.333333
    assert method == "exact_label_permutation_two_sided"


def test_paired_sign_flip_p_value_uses_monte_carlo_for_large_pairs():
    from lynchpin.analysis.machine.controlled_benchmarks import paired_sign_flip_p_value

    p_value, method = paired_sign_flip_p_value(
        tuple((float(i), float(i + 1)) for i in range(20)),
        max_exact_pairs=4,
        iterations=100,
    )

    assert method == "monte_carlo_paired_sign_flip_two_sided"
    assert p_value is not None
    assert 0.0 <= p_value <= 1.0


def test_validate_executed_benchmark_manifest_accepts_completed_run(tmp_path):
    from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest

    internal_json = tmp_path / "nix-internal-json.ndjson"
    internal_json.write_text(
        "\n".join([
            json.dumps({"action": "start", "id": 1, "timestamp": "2026-05-12T12:00:00+00:00"}),
            json.dumps({"action": "stop", "id": 1, "timestamp": "2026-05-12T12:00:01+00:00"}),
        ]),
        encoding="utf-8",
    )
    manifest = {
        "schema": "lynchpin.machine_experiment.run.v1",
        "run_id": "run1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "xtask-stage:test",
        "command": ["xtask", "test"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "monotonic_started_ns": 1_000_000,
        "monotonic_ended_ns": 61_000_000_000,
        "exit_status": 0,
        "execution_outcome": {
            "status": "success",
            "timeout_s": None,
            "censored": False,
            "retry_attempt": 1,
            "warmup_discarded": False,
            "partial_output": False,
        },
        "planned_treatment": _controlled_plan(str(internal_json)),
        "git": {"root": "/realm/project/sinex", "head": "abc123", "branch": "master", "dirty": False},
        "measurement_context": {
            "host_boot_id": "boot1",
            "system_generation": "42",
            "kernel_release": "6.9.0",
            "cpu_governor": "performance",
            "power_profile": "balanced",
            "thermal_zone_policy": "observed",
            "env_digest": {"PATH": "sha256:demo"},
        },
        "pre_state": {},
        "post_state": {},
    }

    validation = validate_executed_benchmark_manifest(
        manifest,
        manifest_path=tmp_path / "manifest.json",
        require_file_refs=True,
    )

    assert validation.valid is True
    assert validation.issues == ()
    assert validation.internal_json_path == str(internal_json)
    assert validation.internal_json_summary is not None
    assert validation.internal_json_summary["phase_count"] == 1
    assert validation.readiness is not None
    assert validation.readiness["controlled"] is True
    assert validation.selected_run is not None
    assert validation.selected_run["run_id"] == "run1"


def test_validate_executed_benchmark_manifest_rejects_selected_run_mismatch(tmp_path):
    from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest

    internal_json = tmp_path / "nix-internal-json.ndjson"
    internal_json.write_text(
        "\n".join([
            json.dumps({"action": "start", "id": 1, "timestamp": "2026-05-12T12:00:00+00:00"}),
            json.dumps({"action": "stop", "id": 1, "timestamp": "2026-05-12T12:00:01+00:00"}),
        ]),
        encoding="utf-8",
    )
    planned = _controlled_plan(str(internal_json))
    planned["selected_run"] = {
        **planned["selected_run"],
        "run_id": "run2",
        "treatment_label": "baseline",
        "telemetry_window_id": "grp1:run2:manifest_timestamps",
    }
    manifest = {
        "schema": "lynchpin.machine_experiment.run.v1",
        "run_id": "run1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "xtask-stage:test",
        "command": ["xtask", "test"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "monotonic_started_ns": 1,
        "monotonic_ended_ns": 2,
        "exit_status": 0,
        "execution_outcome": {
            "status": "success",
            "timeout_s": None,
            "censored": False,
            "retry_attempt": 1,
            "warmup_discarded": False,
            "partial_output": False,
        },
        "planned_treatment": planned,
        "git": {"root": "/realm/project/sinex", "head": "abc123", "branch": "master", "dirty": False},
        "measurement_context": {
            "host_boot_id": "boot1",
            "system_generation": "42",
            "kernel_release": "6.9.0",
            "cpu_governor": "performance",
            "power_profile": "balanced",
            "thermal_zone_policy": "observed",
            "env_digest": {"PATH": "sha256:demo"},
        },
        "pre_state": {},
        "post_state": {},
    }

    validation = validate_executed_benchmark_manifest(manifest, require_file_refs=True)

    assert validation.valid is False
    assert any("does not match executed run_id" in issue for issue in validation.issues)
    assert any("treatment_label" in issue and "randomized_order" in issue for issue in validation.issues)


def test_validate_executed_benchmark_manifest_rejects_templates():
    from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest

    validation = validate_executed_benchmark_manifest({
        "schema": "lynchpin.machine_experiment.template.v1",
        "template_status": "planned_not_executed",
        "run_id": "run1",
        "host": "<fill-host-at-execution>",
        "workload": "xtask-stage:test",
        "command": [],
        "started_at": None,
        "ended_at": None,
        "exit_status": None,
        "planned_treatment": _controlled_plan("<capture-root>/{run_id}/nix-internal-json.ndjson"),
        "git": {"root": None, "head": None, "branch": None, "dirty": None},
        "pre_state": {},
        "post_state": {},
    })

    assert validation.valid is False
    assert "template manifest is not an executed run" in validation.issues
    assert "missing or invalid started_at" in validation.issues
    assert any("internal-json path" in issue and "templated" in issue for issue in validation.issues)


def test_validate_executed_benchmark_manifest_rejects_internal_json_path_drift():
    from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest

    planned = _controlled_plan("/tmp/run1/selected.ndjson")
    planned["controlled_benchmark"]["internal_json"]["path"] = "/tmp/run1/benchmark.ndjson"
    manifest = {
        "schema": "lynchpin.machine_experiment.run.v1",
        "run_id": "run1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "xtask-stage:test",
        "command": ["xtask", "test"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "monotonic_started_ns": 1,
        "monotonic_ended_ns": 2,
        "exit_status": 0,
        "execution_outcome": {
            "status": "success",
            "timeout_s": None,
            "censored": False,
            "retry_attempt": 1,
            "warmup_discarded": False,
            "partial_output": False,
        },
        "planned_treatment": planned,
        "nix_internal_json_path": "/tmp/run1/top.ndjson",
        "git": {"root": "/realm/project/sinex", "head": "abc123", "branch": "master", "dirty": False},
        "measurement_context": {
            "host_boot_id": "boot1",
            "system_generation": "42",
            "kernel_release": "6.9.0",
            "cpu_governor": "performance",
            "power_profile": "balanced",
            "thermal_zone_policy": "observed",
            "env_digest": {"PATH": "sha256:demo"},
        },
        "pre_state": {},
        "post_state": {},
    }

    validation = validate_executed_benchmark_manifest(manifest)

    assert validation.valid is False
    assert any("internal-json path declarations disagree" in issue for issue in validation.issues)


def test_validate_executed_benchmark_manifest_requires_hygiene_contract_fields():
    from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest

    manifest = {
        "schema": "lynchpin.machine_experiment.run.v1",
        "run_id": "run1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "xtask-stage:test",
        "command": ["xtask", "test"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "exit_status": 0,
        "planned_treatment": _controlled_plan("/tmp/run1/nix-internal-json.ndjson"),
        "git": {"root": "/realm/project/sinex", "head": "abc123", "branch": "master", "dirty": False},
        "pre_state": {},
        "post_state": {},
    }

    validation = validate_executed_benchmark_manifest(manifest)

    assert validation.valid is False
    assert "missing or invalid monotonic_started_ns" in validation.issues
    assert "missing execution_outcome" in validation.issues
    assert "missing measurement_context" in validation.issues


def test_validate_executed_benchmark_manifest_allows_structural_internal_json_without_timed_phases(tmp_path):
    from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest

    internal_json = tmp_path / "nix-internal-json.ndjson"
    internal_json.write_text(json.dumps({"action": "result", "activity": 1}) + "\n", encoding="utf-8")
    manifest = {
        "schema": "lynchpin.machine_experiment.run.v1",
        "run_id": "run1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "xtask-stage:test",
        "command": ["xtask", "test"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "monotonic_started_ns": 1,
        "monotonic_ended_ns": 2,
        "exit_status": 0,
        "execution_outcome": {
            "status": "success",
            "timeout_s": None,
            "censored": False,
            "retry_attempt": 1,
            "warmup_discarded": False,
            "partial_output": False,
        },
        "planned_treatment": _controlled_plan(str(internal_json)),
        "git": {"root": "/realm/project/sinex", "head": "abc123", "branch": "master", "dirty": False},
        "measurement_context": {
            "host_boot_id": "boot1",
            "system_generation": "42",
            "kernel_release": "6.9.0",
            "cpu_governor": "performance",
            "power_profile": "balanced",
            "thermal_zone_policy": "observed",
            "env_digest": {"PATH": "sha256:demo"},
        },
        "pre_state": {},
        "post_state": {},
    }

    validation = validate_executed_benchmark_manifest(manifest, require_file_refs=True)

    assert validation.valid is True
    assert "internal-json capture has no complete timed phases" in validation.warnings
    assert validation.internal_json_summary is not None
    assert validation.internal_json_summary["parsed_count"] == 1


def _controlled_plan(internal_json_path: str) -> dict:
    return {
        "controlled_benchmark": {
            "run_group_id": "grp1",
            "derivations": [{"name": "xtask", "drv_path": "/nix/store/xtask.drv"}],
            "cache_conditions": ["cold", "warm"],
            "assignment_seed": 1,
            "randomized_order": [
                {
                    "run_id": "run1",
                    "treatment_label": "baseline",
                    "cache_condition": "cold",
                    "derivation_key": "/nix/store/xtask.drv",
                },
                {
                    "run_id": "run2",
                    "treatment_label": "turbo",
                    "cache_condition": "cold",
                    "derivation_key": "/nix/store/xtask.drv",
                },
                {
                    "run_id": "run3",
                    "treatment_label": "baseline",
                    "cache_condition": "warm",
                    "derivation_key": "/nix/store/xtask.drv",
                },
                {
                    "run_id": "run4",
                    "treatment_label": "turbo",
                    "cache_condition": "warm",
                    "derivation_key": "/nix/store/xtask.drv",
                },
            ],
            "control_label": "baseline",
            "treatment_label": "turbo",
            "internal_json": _internal_json_contract(internal_json_path),
            "telemetry": {"window_source": "manifest_timestamps"},
        },
        "selected_run": {
            "run_id": "run1",
            "sequence_index": 1,
            "treatment_label": "baseline",
            "cache_condition": "cold",
            "derivation_key": "/nix/store/xtask.drv",
            "telemetry_window_id": "grp1:run1:manifest_timestamps",
            "internal_json_path": internal_json_path,
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


def _internal_json_contract(path: str) -> dict:
    return {
        "path": path,
        "log_format": "internal-json",
        "capture_stream": "stderr",
        "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
    }
