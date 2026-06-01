from __future__ import annotations

from lynchpin.core.io import save_json


def test_machine_mechanisms_group_support_assessments(tmp_path):
    from lynchpin.analysis.machine.mechanisms import analyze_machine_mechanisms

    support = tmp_path / "machine_support_assessment.json"
    save_json(
        support,
        {
            "assessments": [
                _assessment("assess1", "cand1", "cohort_contrast:stage=test"),
                _assessment("assess2", "cand2", "cohort_contrast:stage=build"),
            ]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_mechanisms(support_assessment_path=support)

    assert analysis.mechanism_count == 1
    row = analysis.mechanisms[0]
    assert row.mechanism_family == "stage_regression_or_workload_mix"
    assert row.candidate_ids == ("cand1", "cand2")
    assert row.assessment_ids == ("assess1", "assess2")
    assert row.projects == ("sinex",)
    assert row.support_levels == ("insufficient",)
    assert "held-out windows lose the contrast" in row.falsifiers
    assert row.refusal_reasons == ("no executed controlled benchmark claim exists",)


def _assessment(assessment_id: str, candidate_id: str, factor: str) -> dict:
    return {
        "assessment_id": assessment_id,
        "candidate_id": candidate_id,
        "project": "sinex",
        "metric": "stage.duration_s",
        "suspected_factor": factor,
        "support_level": "insufficient",
        "refusal_reasons": ["no executed controlled benchmark claim exists"],
        "mechanism": {
            "mechanism_id": "machine-mechanism:stage_regression_or_workload_mix",
            "mechanism_family": "stage_regression_or_workload_mix",
            "expected_signatures": ["slowdown concentrates in specific command/project/stage cohorts"],
            "falsifiers": ["held-out windows lose the contrast"],
            "discriminating_measurements": ["Nix internal-json phase timing"],
            "current_support_ceiling": "candidate",
            "cheapest_next_action": "capture derivation-bound internal-json logs",
        },
    }
