from __future__ import annotations

import json

from lynchpin.core.io import save_json


def test_selected_benchmark_group_exports_one_ready_group(tmp_path):
    from lynchpin.analysis.machine.benchmark_execution import run_selected_benchmark_group

    candidates = tmp_path / "machine_attribution_candidates.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    preflight = tmp_path / "machine_benchmark_preflight.json"
    support = tmp_path / "machine_support_assessment.json"
    _write_queue_inputs(candidates, bundle, preflight, support)

    result = run_selected_benchmark_group(
        run_group_id="grp1",
        output_dir=tmp_path / "experiments",
        candidates_path=candidates,
        manifest_bundle_path=bundle,
        preflight_path=preflight,
        support_path=support,
        execute=False,
        materialize_after=False,
    )

    assert result.run_group_id == "grp1"
    assert result.candidate_id == "cand1"
    assert result.ready_to_export is True
    assert len(result.run_scripts) == 1
    assert result.run_scripts[0].executed is False
    assert (tmp_path / "experiments/grp1/runs/run1/run.sh").exists()
    assert "bash " in result.next_actions[0]
    assert "machine-experiment-manifests" in result.next_actions[1]


def test_selected_benchmark_group_executes_and_refreshes(monkeypatch, tmp_path):
    from lynchpin.analysis.machine import benchmark_execution as mod
    from lynchpin.analysis.machine.benchmark_execution import run_selected_benchmark_group

    candidates = tmp_path / "machine_attribution_candidates.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    preflight = tmp_path / "machine_benchmark_preflight.json"
    support = tmp_path / "machine_support_assessment.json"
    _write_queue_inputs(candidates, bundle, preflight, support)
    calls: list[tuple[str, ...]] = []

    def fake_run(command, **kwargs):
        calls.append(tuple(str(part) for part in command))
        if len(command) == 2 and command[0] == "bash":
            script = tmp_path / "experiments/grp1/runs/run1/run.sh"
            manifest = script.parent / "manifest.json"
            template = json.loads((script.parent / "manifest.template.json").read_text(encoding="utf-8"))
            template.pop("template_status", None)
            template.update({
                "schema": "lynchpin.machine_experiment.run.v1",
                "host": "sinnix-prime",
                "workload": "xtask-stage:test",
                "command": ["nix", "build", "--log-format", "internal-json", "/nix/store/a.drv"],
                "cwd": "/realm/project/sinex",
                "started_at": "2026-06-05T12:00:00+00:00",
                "ended_at": "2026-06-05T12:00:01+00:00",
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
                "git": {"root": "/realm/project/sinex", "head": "abc", "branch": "master", "dirty": False},
                "measurement_context": {
                    "host_boot_id": "boot",
                    "system_generation": "gen",
                    "kernel_release": "kernel",
                    "cpu_governor": "performance",
                    "power_profile": "balanced",
                    "thermal_zone_policy": "observed",
                    "env_digest": {"PATH": "sha256:demo"},
                },
                "pre_state": {},
                "post_state": {},
            })
            (script.parent / "nix-internal-json.ndjson").write_text(
                "\n".join([
                    json.dumps({
                        "action": "start",
                        "id": 1,
                        "type": "build",
                        "timestamp": "2026-06-05T12:00:00+00:00",
                    }),
                    json.dumps({
                        "action": "stop",
                        "id": 1,
                        "timestamp": "2026-06-05T12:00:01+00:00",
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            manifest.write_text(json.dumps(template), encoding="utf-8")

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "experiment_root", lambda: tmp_path / "canonical-experiments")

    result = run_selected_benchmark_group(
        run_group_id="grp1",
        output_dir=tmp_path / "experiments",
        candidates_path=candidates,
        manifest_bundle_path=bundle,
        preflight_path=preflight,
        support_path=support,
        execute=True,
        materialize_after=True,
    )

    assert result.run_scripts[0].executed is True
    assert result.run_scripts[0].validation_valid is True
    assert len(result.materialization_exit_codes) == 6
    assert (tmp_path / "canonical-experiments/grp1/runs/run1/manifest.json").exists()
    assert calls[0][0] == "bash"
    assert calls[1][1:3] == ("-m", "lynchpin.analysis")
    assert "machine-experiment-manifests" in calls[1]
    assert "machine-promote-experiments" in calls[2]
    assert "machine-experiments" in calls[3]
    assert "--refresh-id" in calls[3]
    assert "machine-readiness" in calls[-1]


def _write_queue_inputs(candidates, bundle, preflight, support) -> None:
    save_json(
        candidates,
        {
            "candidates": [{
                "candidate_id": "cand1",
                "priority_score": 9.0,
                "pareto_frontier": True,
                "metric": "stage.duration_s",
            }]
        },
        sort_keys=True,
    )
    save_json(
        bundle,
        {
            "groups": [{
                "run_group_id": "grp1",
                "candidate_id": "cand1",
                "plan_id": "plan1",
                "primary_metric": "stage.duration_s",
                "run_count": 1,
                "run_templates": [{
                    "run_id": "run1",
                    "run_group_id": "grp1",
                    "sequence_index": 1,
                    "treatment_label": "baseline",
                    "cache_condition": "cold",
                    "derivation_key": "/nix/store/a.drv",
                    "telemetry_window_id": "grp1:run1:manifest_timestamps",
                    "manifest": {
                        "schema": "lynchpin.machine_experiment.template.v1",
                        "template_status": "planned_not_executed",
                        "run_id": "run1",
                        "run_group_id": "grp1",
                        "host": "<fill-host-at-execution>",
                        "workload": "xtask-stage:test",
                        "command": [],
                        "planned_treatment": {
                            "controlled_benchmark": {
                                "run_group_id": "grp1",
                                "derivations": [{"drv_path": "/nix/store/a.drv"}],
                                "cache_conditions": ["cold", "warm"],
                                "assignment_seed": 1,
                                "randomized_order": [{
                                    "run_id": "run1",
                                    "treatment_label": "baseline",
                                    "cache_condition": "cold",
                                    "derivation_key": "/nix/store/a.drv",
                                }],
                                "control_label": "baseline",
                                "treatment_label": "baseline",
                                "internal_json": {
                                    "path": "{run_dir}/nix-internal-json.ndjson",
                                    "log_format": "internal-json",
                                    "capture_stream": "stderr",
                                    "argv_template": ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
                                },
                                "telemetry": {"window_source": "manifest_timestamps"},
                                "pre_analysis": {
                                    "research_question": "demo",
                                    "hypothesis": "demo",
                                    "estimand": "demo",
                                    "unit": "run",
                                    "primary_metric": "stage.duration_s",
                                    "minimum_effect_of_interest": 1,
                                    "inclusion_rules": ["fixed derivation set"],
                                    "exclusion_rules": ["missing internal-json"],
                                    "blocking_keys": ["cache_condition"],
                                    "support_ceiling": "controlled",
                                    "selected_design_variant": "blocked_randomization",
                                    "causal_model": {
                                        "treatment_variable": "benchmark treatment",
                                        "outcome_variable": "stage.duration_s",
                                        "blocking_variables": ["cache_condition"],
                                        "adjustment_variables": ["derivation"],
                                        "forbidden_post_treatment_variables": ["duration"],
                                        "known_unobserved_confounders": [],
                                        "identification_note": "randomized manifest fixture",
                                    },
                                    "instrumentation_bundle": {"internal_json": "required"},
                                    "power_note": {"minimum_runs": 1},
                                    "design_variants": [{"design_id": "blocked_randomization"}],
                                },
                            },
                            "selected_run": {
                                "run_id": "run1",
                                "sequence_index": 1,
                                "treatment_label": "baseline",
                                "cache_condition": "cold",
                                "derivation_key": "/nix/store/a.drv",
                                "telemetry_window_id": "grp1:run1:manifest_timestamps",
                                "internal_json_path": "{run_dir}/nix-internal-json.ndjson",
                            },
                        },
                        "git": {"root": None, "head": None, "branch": None, "dirty": None},
                        "pre_state": {},
                        "post_state": {},
                    },
                }],
            }]
        },
        sort_keys=True,
    )
    save_json(
        preflight,
        {
            "groups": [{
                "run_group_id": "grp1",
                "run_count": 1,
                "ready_run_count": 1,
                "issue_count": 0,
                "warning_count": 0,
                "treatments": ["baseline"],
                "cache_conditions": ["cold"],
            }]
        },
        sort_keys=True,
    )
    save_json(
        support,
        {
            "assessments": [{
                "candidate_id": "cand1",
                "support_level": "insufficient",
                "refusal_reasons": ["no executed controlled run"],
            }]
        },
        sort_keys=True,
    )
