from __future__ import annotations

import json

from typer.testing import CliRunner

from lynchpin.analysis import cli
from lynchpin.analysis.machine import status as machine_status
from lynchpin.core.io import save_json


def test_machine_status_summarizes_generated_artifacts(monkeypatch, tmp_path):
    save_json(
        tmp_path / "machine_support_assessment.json",
        {
            "candidate_count": 3,
            "refusal_count": 1,
            "controlled_claim_count": 0,
            "natural_experiment_support_count": 2,
            "assessments": [
                {"support_level": "natural_experiment"},
                {"support_level": "natural_experiment"},
                {"support_level": "insufficient"},
            ],
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_experiment_claims.json",
        {
            "run_count": 3,
            "controlled_claim_count": 0,
            "observational_claim_count": 3,
            "claim_packs": [
                {"manifest_validation": {"valid": True}},
                {"manifest_validation": {"valid": False}},
                {"manifest_validation": {}},
            ],
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_attribution_claims.json",
        {"claim_count": 3, "by_support_level": {"insufficient": 1, "natural_experiment": 2}},
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_instrumentation_gaps.json",
        {
            "gap_count": 2,
            "by_missing_source": {"controlled_benchmark_run": 1, "negative_control_check": 1},
            "by_mechanism_family": {"resource_contention": 2},
            "gaps": [
                {"next_action": "execute the approved manifest and promote run logs/telemetry"},
                {"next_action": "collect or derive the missing placebo/control check for the matched design"},
            ],
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_benchmark_preflight.json",
        {"run_count": 12, "ready_run_count": 12, "issue_count": 0, "warning_count": 12},
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_benchmark_execution_queue.json",
        {
            "queue_count": 2,
            "ready_group_count": 2,
            "blocked_group_count": 0,
            "run_template_count": 12,
            "ready_run_count": 12,
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_below_export_queue.json",
        {
            "queue_count": 4,
            "failed_capture_count": 1,
            "root": "/realm/data/captures/stability-lab",
            "live_store": "/realm/data/captures/machine/below/store",
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_experiment_manifest_diagnostics.json",
        {
            "manifest_count": 2,
            "source_loadable_count": 1,
            "controlled_benchmark_valid_count": 0,
            "validation_issue_count": 1,
            "promotion_issue_count": 0,
            "controlled_run_invalid_count": 1,
            "legacy_observational_count": 0,
            "template_count": 1,
            "out_of_window_count": 0,
            "by_kind": {"executed_run": 1, "template": 1},
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_dataset_diagnostics.json",
        {
            "feature_audit": {"status": "ready_for_mining"},
            "mining_audit": {"multiplicity_status": "registered"},
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_measurement_system.json",
        {"check_count": 5, "by_status": {"failed": 1, "passed": 4}},
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_assumption_checks.json",
        {
            "check_count": 8,
            "by_status": {"failed": 2, "passed": 6},
            "checks": [
                {"check_status": "failed", "support_level": "natural_experiment", "claim_scope": "negative_controls"},
                {"check_status": "failed", "support_level": "insufficient", "claim_scope": "claim_support"},
                {"check_status": "passed", "support_level": "controlled", "claim_scope": "controlled_design"},
            ],
        },
        sort_keys=True,
    )
    save_json(
        tmp_path / "machine_analysis_readiness.json",
        {"dimensions": [{"dimension": "telemetry", "status": "stable"}, {"dimension": "support_gate", "status": "limited"}]},
        sort_keys=True,
    )
    monkeypatch.setattr(machine_status, "resolve_analysis_path", lambda name: str(tmp_path / name))

    result = CliRunner().invoke(cli.build_app(), ["machine-status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["artifacts"]["available"] == 12
    assert payload["support"]["executed_controlled_claim_count"] == 0
    assert payload["support"]["assessment_natural_experiment"] == 2
    assert payload["support"]["natural_experiment"] == 2
    assert payload["experiments"]["by_manifest_validation_status"] == {
        "invalid": 1,
        "unknown": 1,
        "valid": 1,
    }
    assert payload["benchmark_preflight"]["ready_run_count"] == 12
    assert payload["benchmark_preflight"]["issue_count"] == 0
    assert payload["benchmark_execution_queue"]["ready_group_count"] == 2
    assert payload["below_export_queue"]["queue_count"] == 4
    assert payload["below_export_queue"]["failed_capture_count"] == 1
    assert payload["experiment_manifests"]["source_loadable_count"] == 1
    assert payload["experiment_manifests"]["controlled_run_invalid_count"] == 1
    assert payload["gaps"]["gap_count"] == 2
    assert payload["gaps"]["by_missing_source"] == {
        "controlled_benchmark_run": 1,
        "negative_control_check": 1,
    }
    assert payload["claims"]["by_support_level"] == {"insufficient": 1, "natural_experiment": 2}
    assert payload["measurement"]["by_status"] == {"failed": 1, "passed": 4}
    assert payload["assumptions"]["by_status"] == {"failed": 2, "passed": 6}
    assert payload["assumptions"]["failed_by_support_level"] == {"insufficient": 1, "natural_experiment": 1}
    assert payload["assumptions"]["failed_by_scope"] == {"claim_support": 1, "negative_controls": 1}
    assert payload["readiness"]["unstable_dimensions"] == [
        {"dimension": "support_gate", "status": "limited", "caveats": []}
    ]
    assert "experiment manifests exist but no controlled benchmark claim is currently proven" in payload["blockers"]
    assert "1 executed benchmark manifests are invalid" in payload["blockers"]
    assert "1 measurement-system diagnostics failed" in payload["blockers"]
    assert "1 supported attribution assumption checks failed" in payload["blockers"]
    assert "1 refused-claim assumption checks failed" not in payload["blockers"]
    assert "readiness dimension support_gate is limited" in payload["blockers"]
