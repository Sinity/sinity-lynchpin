from __future__ import annotations

import json


def test_machine_benchmarks_export_cli_writes_templates(tmp_path):
    from lynchpin.cli.machine_benchmarks import main

    plans = tmp_path / "plans.json"
    plans.write_text(
        json.dumps({
            "plans": [{
                "plan_id": "plan1",
                "candidate_id": "cand1",
                "planning_status": "ready",
                "support_ceiling": "controlled",
                "primary_metric": "stage.duration_s",
                "manifest_preview": {
                    "workload": "xtask-stage:test",
                    "controlled_benchmark": {"run_group_id": "grp1"},
                    "pre_analysis": {},
                },
                "run_manifest": [{
                    "run_id": "run1",
                    "run_group_id": "grp1",
                    "sequence_index": 1,
                    "treatment_label": "baseline",
                    "cache_condition": "cold",
                    "telemetry_window_id": "grp1:run1:manifest_timestamps",
                }],
            }]
        }),
        encoding="utf-8",
    )

    code = main(["export", "--plans", str(plans), "--output", str(tmp_path / "out")])

    assert code == 0
    template = tmp_path / "out/grp1/runs/run1/manifest.template.json"
    assert template.exists()
    assert (tmp_path / "out/grp1/runs/run1/run.sh").exists()
    payload = json.loads(template.read_text(encoding="utf-8"))
    assert payload["nix_internal_json_path"] == str(tmp_path / "out/grp1/runs/run1/nix-internal-json.ndjson")


def test_machine_benchmarks_validate_cli_rejects_templates(tmp_path, capsys):
    from lynchpin.cli.machine_benchmarks import main

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({
            "schema": "lynchpin.machine_experiment.template.v1",
            "template_status": "planned_not_executed",
            "run_id": "run1",
            "host": "<fill-host-at-execution>",
            "workload": "xtask-stage:test",
            "command": [],
            "started_at": None,
            "ended_at": None,
            "exit_status": None,
            "planned_treatment": {},
            "git": {"root": None, "head": None, "branch": None, "dirty": None},
            "pre_state": {},
            "post_state": {},
        }),
        encoding="utf-8",
    )

    code = main(["validate", str(tmp_path)])

    assert code == 1
    assert "invalid" in capsys.readouterr().out


def test_machine_benchmarks_validate_cli_accepts_completed_manifest(tmp_path):
    from lynchpin.cli.machine_benchmarks import main

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({
            "schema": "lynchpin.machine_experiment.run.v1",
            "run_id": "run1",
            "run_group_id": "grp1",
            "host": "sinnix-prime",
            "workload": "xtask-stage:test",
            "command": ["xtask", "test"],
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
            "planned_treatment": {},
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
        }),
        encoding="utf-8",
    )

    assert main(["validate", str(tmp_path)]) == 0
