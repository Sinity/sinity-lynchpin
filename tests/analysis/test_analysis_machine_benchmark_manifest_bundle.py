from __future__ import annotations

import json

from lynchpin.core.io import save_json


def test_benchmark_manifest_bundle_exports_templates_without_executed_manifest(tmp_path):
    from lynchpin.analysis.machine.benchmark_manifest_bundle import (
        analyze_machine_benchmark_manifest_bundle,
        export_machine_benchmark_manifest_bundle,
    )

    plans = tmp_path / "machine_benchmark_plans.json"
    save_json(
        plans,
        {
            "plans": [{
                "plan_id": "plan1",
                "candidate_id": "cand1",
                "planning_status": "ready",
                "support_ceiling": "controlled",
                "primary_metric": "stage.duration_s",
                "manifest_preview": {
                    "workload": "xtask-stage:test",
                    "controlled_benchmark": {"run_group_id": "grp1", "metric": "stage.duration_s"},
                    "pre_analysis": {"estimand": "delta"},
                },
                "run_manifest": [{
                    "run_id": "run1",
                    "run_group_id": "grp1",
                    "sequence_index": 1,
                    "treatment_label": "baseline",
                    "cache_condition": "cold",
                    "derivation_key": "/nix/store/xtask.drv",
                    "internal_json_path": "/tmp/run1/internal.ndjson",
                    "telemetry_window_id": "grp1:run1:manifest_timestamps",
                }],
                "caveats": ["review before execution"],
            }]
        },
        sort_keys=True,
    )

    bundle = analyze_machine_benchmark_manifest_bundle(plans_path=plans)
    written = export_machine_benchmark_manifest_bundle(bundle, tmp_path / "out")

    assert bundle.group_count == 1
    assert bundle.run_template_count == 1
    assert len(written) == 3
    assert (tmp_path / "out/grp1/plan.json").exists()
    template_path = tmp_path / "out/grp1/runs/run1/manifest.template.json"
    assert template_path.exists()
    assert not (tmp_path / "out/grp1/runs/run1/manifest.json").exists()
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert template["template_status"] == "planned_not_executed"
    assert template["started_at"] is None
    assert template["planned_treatment"]["selected_run"]["derivation_key"] == "/nix/store/xtask.drv"
    internal_json_path = str(tmp_path / "out/grp1/runs/run1/nix-internal-json.ndjson")
    assert template["nix_internal_json_path"] == internal_json_path
    assert template["planned_treatment"]["selected_run"]["internal_json_path"] == internal_json_path
    assert template["planned_treatment"]["controlled_benchmark"]["internal_json"]["path"] == internal_json_path
    assert template["planned_treatment"]["controlled_benchmark"]["internal_json"]["log_format"] == "internal-json"
    assert template["planned_treatment"]["controlled_benchmark"]["internal_json"]["capture_stream"] == "stderr"
    assert "<capture-root>" not in json.dumps(template)
    runner = tmp_path / "out/grp1/runs/run1/run.sh"
    assert runner.exists()
    assert runner.stat().st_mode & 0o111
    runner_text = runner.read_text(encoding="utf-8")
    assert "manifest.template.json" in runner_text
    assert "manifest.json" in runner_text
    assert "prepare_cache_condition" in runner_text
    assert "warmup-nix-internal-json.ndjson" in runner_text
    assert "cache_condition=cold" in runner_text
    assert "nix build --log-format internal-json /nix/store/xtask.drv" in runner_text
    assert "refusing to overwrite existing $manifest" in runner_text
