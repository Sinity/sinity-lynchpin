from __future__ import annotations

import json
from datetime import date

from lynchpin.analysis.machine.experiment_manifest_diagnostics import (
    analyze_machine_experiment_manifest_diagnostics,
)


def test_experiment_manifest_diagnostics_separates_source_and_controlled_validity(tmp_path):
    _write(tmp_path / "smoke" / "manifest.json", {
        "run_id": "smoke",
        "host": "sinnix-prime",
        "workload": "manual-smoke",
        "command": ["true"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:00:01+00:00",
        "pre_state": {},
        "post_state": {},
    })
    _write(tmp_path / "grp1" / "runs" / "run1" / "manifest.json", _controlled_manifest())
    _write(tmp_path / "template" / "manifest.json", {
        "schema": "lynchpin.machine_experiment.template.v1",
        "template_status": "planned_not_executed",
        "run_id": "planned",
        "started_at": "2026-05-12T12:00:00+00:00",
    })
    invalid = tmp_path / "invalid" / "manifest.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("{", encoding="utf-8")

    analysis = analyze_machine_experiment_manifest_diagnostics(root=tmp_path)

    assert analysis.manifest_count == 4
    assert analysis.source_loadable_count == 2
    assert analysis.controlled_benchmark_valid_count == 1
    assert analysis.template_count == 1
    assert analysis.validation_issue_count == 3
    assert analysis.promotion_issue_count == 1
    assert analysis.controlled_run_invalid_count == 0
    assert analysis.legacy_observational_count == 1
    by_path = {row.relative_path: row for row in analysis.diagnostics}
    assert by_path["smoke/manifest.json"].source_loadable is True
    assert by_path["smoke/manifest.json"].controlled_benchmark_valid is False
    assert by_path["grp1/runs/run1/manifest.json"].controlled_benchmark_valid is True
    assert by_path["template/manifest.json"].manifest_kind == "template"
    assert by_path["invalid/manifest.json"].manifest_kind == "unreadable_json"


def test_experiment_manifest_diagnostics_marks_window_exclusions(tmp_path):
    _write(tmp_path / "grp1" / "runs" / "run1" / "manifest.json", _controlled_manifest())

    analysis = analyze_machine_experiment_manifest_diagnostics(
        root=tmp_path,
        start=None,
        end=None,
    )
    bounded = analyze_machine_experiment_manifest_diagnostics(
        root=tmp_path,
        start=date(2026, 5, 13),
        end=date(2026, 5, 13),
    )

    assert analysis.source_loadable_count == 1
    assert bounded.source_loadable_count == 0
    assert bounded.out_of_window_count == 1
    assert bounded.diagnostics[0].in_window is False


def _write(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _controlled_manifest() -> dict:
    selected = {
        "run_id": "run1",
        "sequence_index": 1,
        "treatment_label": "baseline",
        "cache_condition": "warm",
        "derivation_key": "/nix/store/demo.drv",
        "telemetry_window_id": "grp1:run1:manifest_timestamps",
        "internal_json_path": "nix-internal-json.ndjson",
    }
    treatment = {
        **selected,
        "run_id": "run2",
        "sequence_index": 2,
        "treatment_label": "candidate",
        "cache_condition": "cold",
        "telemetry_window_id": "grp1:run2:manifest_timestamps",
    }
    return {
        "schema": "lynchpin.machine_experiment.run.v1",
        "run_id": "run1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "xtask-stage:test",
        "command": ["nix", "build", "/nix/store/demo.drv"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "monotonic_started_ns": 1,
        "monotonic_ended_ns": 60_000_000_000,
        "exit_status": 0,
        "execution_outcome": {
            "status": "success",
            "timeout_s": None,
            "censored": False,
            "retry_attempt": 1,
            "warmup_discarded": False,
            "partial_output": False,
        },
        "planned_treatment": {
            "selected_run": selected,
            "controlled_benchmark": {
                "run_group_id": "grp1",
                "control_label": "baseline",
                "treatment_label": "candidate",
                "cache_conditions": ["warm", "cold"],
                "assignment_seed": 1,
                "derivations": [{"drv_path": "/nix/store/demo.drv"}],
                "randomized_order": [selected, treatment],
                "telemetry": {"window_source": "manifest_timestamps"},
                "internal_json": {
                    "path": "nix-internal-json.ndjson",
                    "log_format": "internal-json",
                    "capture_stream": "stderr",
                    "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
                },
            },
            "pre_analysis": {
                "research_question": "does candidate alter duration?",
                "hypothesis": "candidate changes duration",
                "estimand": "median duration delta",
                "unit": "benchmark run",
                "primary_metric": "duration_s",
                "inclusion_rules": ["completed manifest"],
                "exclusion_rules": ["nonzero exit unless planned"],
                "minimum_effect_of_interest": {"value": 1},
                "blocking_keys": ["cache_condition"],
                "support_ceiling": "controlled",
                "causal_model": {"design": "blocked randomized benchmark"},
                "instrumentation_bundle": {"name": "build_phase"},
                "power_note": {"repeats_per_cell": 1},
            },
        },
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
