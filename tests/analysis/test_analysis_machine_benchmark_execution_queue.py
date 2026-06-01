from __future__ import annotations

from lynchpin.core.io import save_json


def test_benchmark_execution_queue_joins_candidates_preflight_and_support(tmp_path):
    from lynchpin.analysis.machine.benchmark_execution_queue import (
        analyze_machine_benchmark_execution_queue,
    )

    candidates = tmp_path / "machine_attribution_candidates.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    preflight = tmp_path / "machine_benchmark_preflight.json"
    support = tmp_path / "machine_support_assessment.json"
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
                "run_count": 2,
                "run_templates": [
                    {"run_id": "run1", "derivation_key": "/nix/store/a.drv"},
                    {"run_id": "run2", "derivation_key": "/nix/store/b.drv"},
                ],
            }]
        },
        sort_keys=True,
    )
    save_json(
        preflight,
        {
            "groups": [{
                "run_group_id": "grp1",
                "run_count": 2,
                "ready_run_count": 2,
                "issue_count": 0,
                "warning_count": 2,
                "treatments": ["baseline", "candidate"],
                "cache_conditions": ["cold", "warm"],
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
                "instrumentation_gaps": [{
                    "next_action": "execute the approved manifest and promote run logs/telemetry"
                }],
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_benchmark_execution_queue(
        candidates_path=candidates,
        manifest_bundle_path=bundle,
        preflight_path=preflight,
        support_path=support,
    )

    assert analysis.queue_count == 1
    assert analysis.ready_group_count == 1
    item = analysis.items[0]
    assert item.candidate_id == "cand1"
    assert item.ready_to_export is True
    assert item.pareto_frontier is True
    assert item.derivation_keys == ("/nix/store/a.drv", "/nix/store/b.drv")
    assert item.next_action == "execute the approved manifest and promote run logs/telemetry"
