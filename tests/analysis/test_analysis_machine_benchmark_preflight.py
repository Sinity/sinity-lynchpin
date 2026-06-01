from __future__ import annotations

from lynchpin.analysis.machine.benchmark_preflight import analyze_machine_benchmark_preflight
from lynchpin.core.io import save_json


def test_benchmark_preflight_accepts_ready_template_bundle(tmp_path):
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    manifest = _template_manifest()
    save_json(
        bundle,
        {
            "groups": [{
                "run_group_id": "grp1",
                "candidate_id": "cand1",
                "plan_id": "plan1",
                "run_templates": [{
                    "run_id": "run1",
                    "sequence_index": 1,
                    "treatment_label": "baseline",
                    "cache_condition": "warm",
                    "derivation_key": "/nix/store/demo.drv",
                    "manifest": manifest,
                }],
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_benchmark_preflight(manifest_bundle_path=bundle)

    assert analysis.group_count == 1
    assert analysis.run_count == 1
    assert analysis.ready_run_count == 1
    assert analysis.issue_count == 0
    assert analysis.warning_count == 2
    run = analysis.groups[0].runs[0]
    assert run.ready_to_export is True
    assert set(run.warnings) == {
        "internal-json path is templated until export",
        "controlled_benchmark.internal_json.path is templated until export",
    }


def test_benchmark_preflight_rejects_selected_run_drift(tmp_path):
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    manifest = _template_manifest()
    manifest["planned_treatment"]["selected_run"]["run_id"] = "other-run"
    save_json(
        bundle,
        {
            "groups": [{
                "run_group_id": "grp1",
                "candidate_id": "cand1",
                "plan_id": "plan1",
                "run_templates": [{
                    "run_id": "run1",
                    "sequence_index": 1,
                    "treatment_label": "baseline",
                    "cache_condition": "warm",
                    "derivation_key": "/nix/store/demo.drv",
                    "manifest": manifest,
                }],
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_benchmark_preflight(manifest_bundle_path=bundle)

    assert analysis.ready_run_count == 0
    assert analysis.issue_count == 2
    assert "does not match executed run_id" in analysis.groups[0].runs[0].issues[0]


def _template_manifest() -> dict:
    selected = {
        "run_id": "run1",
        "sequence_index": 1,
        "treatment_label": "baseline",
        "cache_condition": "warm",
        "derivation_key": "/nix/store/demo.drv",
        "telemetry_window_id": "grp1:run1:manifest_timestamps",
        "internal_json_path": "bench/grp1/{run_id}/nix-internal-json.ndjson",
    }
    treatment = {
        **selected,
        "run_id": "run2",
        "sequence_index": 2,
        "treatment_label": "candidate",
        "telemetry_window_id": "grp1:run2:manifest_timestamps",
    }
    return {
        "schema": "lynchpin.machine_experiment.template.v1",
        "template_status": "planned_not_executed",
        "run_id": "run1",
        "run_group_id": "grp1",
        "host": "<fill-host-at-execution>",
        "workload": "demo",
        "command": [],
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
                    "path": "bench/grp1/{run_id}/nix-internal-json.ndjson",
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
                "causal_model": {
                    "treatment_variable": "candidate",
                    "outcome_variable": "duration_s",
                    "blocking_variables": ["cache_condition", "derivation"],
                    "adjustment_variables": ["host", "software_revision", "pre_window_pressure"],
                    "forbidden_post_treatment_variables": ["during_run_phase_duration", "post_state"],
                    "known_unobserved_confounders": ["thermal carryover"],
                    "identification_note": "blocked randomized template over fixed derivations",
                },
                "instrumentation_bundle": {"name": "build_phase"},
                "power_note": {"repeats_per_cell": 1},
            },
        },
    }
