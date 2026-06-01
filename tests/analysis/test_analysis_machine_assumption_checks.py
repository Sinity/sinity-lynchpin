from __future__ import annotations

from lynchpin.core.io import save_json


def test_machine_assumption_checks_expand_refusal_reasons_and_gaps(tmp_path):
    from lynchpin.analysis.machine.assumption_checks import analyze_machine_assumption_checks

    claims = tmp_path / "machine_attribution_claims.json"
    save_json(
        claims,
        {
            "claims": [{
                "claim_id": "claim1",
                "support_level": "insufficient",
                "source_ids": ["assess1", "cand1"],
                "payload": {
                    "estimate": {
                        "refusal_reasons": ["no executed controlled benchmark claim exists"],
                        "instrumentation_gaps": [{"missing": "executed_controlled_run"}],
                    }
                },
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_assumption_checks(claims_path=claims)

    assert analysis.check_count == 2
    assert analysis.by_status == {"failed": 1, "untestable": 1}
    assert {row.claim_scope for row in analysis.checks} == {"claim_support", "measurement"}
    assert all(row.claim_id == "claim1" for row in analysis.checks)


def test_machine_assumption_checks_emit_controlled_design_pass(tmp_path):
    from lynchpin.analysis.machine.assumption_checks import analyze_machine_assumption_checks

    claims = tmp_path / "machine_attribution_claims.json"
    save_json(
        claims,
        {
            "claims": [{
                "claim_id": "claim2",
                "support_level": "controlled",
                "source_ids": ["run1", "run2"],
                "payload": {"estimate": {"delta": -2.0, "ci_low": -3.0, "ci_high": -1.0, "control_n": 2, "treatment_n": 2}},
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_assumption_checks(claims_path=claims)

    assert analysis.check_count == 4
    assert analysis.by_status == {"passed": 4}
    assert {row.claim_scope for row in analysis.checks} == {
        "controlled_design",
        "measurement",
        "precision",
        "sample_support",
    }


def test_machine_assumption_checks_emit_natural_experiment_passes(tmp_path):
    from lynchpin.analysis.machine.assumption_checks import analyze_machine_assumption_checks

    claims = tmp_path / "machine_attribution_claims.json"
    save_json(
        claims,
        {
            "claims": [{
                "claim_id": "claim-natural",
                "support_level": "natural_experiment",
                "source_ids": ["assess1", "cand1", "design1", "boundary1"],
                "payload": {
                    "estimate": {
                        "source_artifacts": [
                            "machine_matched_designs.json",
                            "machine_negative_controls.json",
                        ]
                    }
                },
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_assumption_checks(claims_path=claims)

    assert analysis.check_count == 3
    assert analysis.by_status == {"passed": 3}
    assert {row.claim_scope for row in analysis.checks} == {
        "dataset_diagnostics",
        "natural_experiment_design",
        "negative_controls",
    }


def test_machine_assumption_checks_flag_controlled_precision_and_caveat_failures(tmp_path):
    from lynchpin.analysis.machine.assumption_checks import analyze_machine_assumption_checks

    claims = tmp_path / "machine_attribution_claims.json"
    save_json(
        claims,
        {
            "claims": [{
                "claim_id": "claim3",
                "support_level": "controlled",
                "source_ids": ["run1"],
                "caveats": ["no machine telemetry samples overlap the run window"],
                "payload": {"estimate": {"delta": 2.0, "ci_low": 5.0, "ci_high": 1.0, "control_n": 0, "treatment_n": 1}},
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_assumption_checks(claims_path=claims)

    by_scope = {row.claim_scope: row for row in analysis.checks}
    assert by_scope["sample_support"].check_status == "failed"
    assert by_scope["precision"].check_status == "failed"
    assert by_scope["measurement"].check_status == "failed"
