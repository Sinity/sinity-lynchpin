from __future__ import annotations


def test_causal_model_assessment_flags_post_treatment_adjustment():
    from lynchpin.analysis.machine.causal_model import assess_causal_model

    assessment = assess_causal_model(
        {
            "treatment_variable": "turbo",
            "outcome_variable": "duration_seconds",
            "blocking_variables": ["cache_condition"],
            "adjustment_variables": ["post_state", "host"],
            "forbidden_post_treatment_variables": ["post_state"],
            "known_unobserved_confounders": ["thermal carryover"],
        },
        support_ceiling="controlled",
    )

    assert assessment.status == "failed"
    assert assessment.treatment_variable == "turbo"
    assert assessment.outcome_variable == "duration_seconds"
    assert assessment.adjustment_variables == ("post_state", "host")
    assert any("post_state" in issue for issue in assessment.issues)
    assert any("thermal carryover" in warning for warning in assessment.warnings)


def test_causal_model_assessment_warns_without_decorative_failure():
    from lynchpin.analysis.machine.causal_model import assess_causal_model

    assessment = assess_causal_model(
        {"treatment_variable": "turbo", "outcome_variable": "duration_seconds"},
        support_ceiling="observational",
    )

    assert assessment.status == "passed"
    assert "causal_model has no blocking_variables; support depends on unblocked comparability" in assessment.warnings
    assert "causal_model has no adjustment_variables; observational support should remain capped" in assessment.warnings
