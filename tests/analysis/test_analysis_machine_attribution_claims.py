from __future__ import annotations

from datetime import date

import pytest


def test_machine_attribution_claim_maps_to_generic_claim_row() -> None:
    from lynchpin.analysis.machine.attribution_claims import MachineAttributionClaim

    claim = MachineAttributionClaim(
        claim_type="machine_attribution",
        project="sinex",
        date=date(2026, 5, 9),
        metric="xtask.check.duration_s",
        effect_kind="software_revision",
        support_level="natural_experiment",
        confidence=0.72,
        summary="check duration increased across a revision boundary",
        baseline={"git_commit": "a", "median_s": 41.4},
        comparison={"git_commit": "b", "median_s": 109.7},
        estimate={"delta": 68.3},
        source_ids=("xtask:archive1:1", "xtask:archive1:2"),
        caveats=("cache state not isolated",),
    )

    row = claim.to_analysis_claim()

    assert row.claim_type == "machine_attribution"
    assert row.project == "sinex"
    assert row.support_level == "natural_experiment"
    assert row.confidence == 0.72
    assert row.score == 68.3
    assert row.source_ids == ("xtask:archive1:1", "xtask:archive1:2")
    assert row.payload["metric"] == "xtask.check.duration_s"
    assert row.payload["effect_kind"] == "software_revision"


def test_machine_attribution_claim_rejects_invalid_confidence() -> None:
    from lynchpin.analysis.machine.attribution_claims import MachineAttributionClaim

    claim = MachineAttributionClaim(
        claim_type="machine_attribution",
        project=None,
        date=None,
        metric="x",
        effect_kind="contention",
        support_level="observational",
        confidence=1.2,
        summary="bad",
        baseline={},
        comparison={},
        estimate={},
    )

    with pytest.raises(ValueError, match="confidence"):
        claim.to_analysis_claim()


def test_machine_attribution_claim_analysis_promotes_refusals(tmp_path) -> None:
    from lynchpin.analysis.machine.attribution_claims import analyze_machine_attribution_claims
    from lynchpin.core.io import save_json

    support = tmp_path / "machine_support_assessment.json"
    experiments = tmp_path / "machine_experiment_claims.json"
    save_json(
        support,
        {
            "assessments": [{
                "assessment_id": "assess1",
                "candidate_id": "cand1",
                "project": "sinex",
                "metric": "stage.duration_s",
                "suspected_factor": "cohort_contrast:stage=test",
                "support_level": "insufficient",
                "confidence": 0.9,
                "decision": "refuse_claim",
                "refusal_reasons": ["no executed controlled benchmark claim exists"],
                "summary": "Refuse causal claim for test stage",
            }]
        },
        sort_keys=True,
    )
    save_json(experiments, {"effect_estimates": [], "claim_packs": []}, sort_keys=True)

    analysis = analyze_machine_attribution_claims(
        support_assessment_path=support,
        experiment_claims_path=experiments,
    )

    assert analysis.claim_count == 1
    assert analysis.by_support_level == {"insufficient": 1}
    row = analysis.claims[0]
    assert row["claim_type"] == "machine_attribution"
    assert row["support_level"] == "insufficient"
    assert row["project"] == "sinex"
    assert row["payload"]["estimate"]["refusal_reasons"] == ["no executed controlled benchmark claim exists"]


def test_machine_attribution_claim_analysis_preserves_support_sources(tmp_path) -> None:
    from lynchpin.analysis.machine.attribution_claims import analyze_machine_attribution_claims
    from lynchpin.core.io import save_json

    support = tmp_path / "machine_support_assessment.json"
    experiments = tmp_path / "machine_experiment_claims.json"
    save_json(
        support,
        {
            "assessments": [{
                "assessment_id": "assess1",
                "candidate_id": "cand1",
                "project": "sinex",
                "metric": "stage.duration_s",
                "suspected_factor": "git_boundary:test",
                "support_level": "natural_experiment",
                "confidence": 0.6,
                "decision": "promote_claim_candidate",
                "refusal_reasons": [],
                "instrumentation_gaps": [],
                "source_artifacts": ["machine_matched_designs.json", "machine_negative_controls.json"],
                "source_ids": ["design1", "boundary1"],
                "summary": "Natural-experiment design support available",
            }]
        },
        sort_keys=True,
    )
    save_json(experiments, {"effect_estimates": [], "claim_packs": []}, sort_keys=True)

    analysis = analyze_machine_attribution_claims(
        support_assessment_path=support,
        experiment_claims_path=experiments,
    )

    row = analysis.claims[0]
    assert row["support_level"] == "natural_experiment"
    assert row["source_ids"] == ["assess1", "cand1", "design1", "boundary1"]
    assert row["payload"]["estimate"]["source_artifacts"] == [
        "machine_matched_designs.json",
        "machine_negative_controls.json",
    ]


def test_machine_attribution_claim_analysis_promotes_controlled_estimates(tmp_path) -> None:
    from lynchpin.analysis.machine.attribution_claims import analyze_machine_attribution_claims
    from lynchpin.core.io import save_json

    support = tmp_path / "machine_support_assessment.json"
    experiments = tmp_path / "machine_experiment_claims.json"
    save_json(support, {"assessments": []}, sort_keys=True)
    save_json(
        experiments,
        {
            "effect_estimates": [{
                "run_group_id": "grp1",
                "metric": "duration_seconds",
                "control_label": "baseline",
                "treatment_label": "turbo",
                "control_n": 3,
                "treatment_n": 3,
                "control_mean": 10.0,
                "treatment_mean": 8.0,
                "delta": -2.0,
                "ci_low": -3.0,
                "ci_high": -1.0,
                "p_value": 0.125,
                "p_value_method": "exact_label_permutation_two_sided",
            }],
            "claim_packs": [{
                "run_id": "run1",
                "run_group_id": "grp1",
                "git_root": "/realm/project/sinex",
                "started_at": "2026-05-01T12:00:00+00:00",
                "caveats": [],
            }],
        },
        sort_keys=True,
    )

    analysis = analyze_machine_attribution_claims(
        support_assessment_path=support,
        experiment_claims_path=experiments,
    )

    assert analysis.claim_count == 1
    assert analysis.by_support_level == {"controlled": 1}
    row = analysis.claims[0]
    assert row["support_level"] == "controlled"
    assert row["date"] == "2026-05-01"
    assert row["source_ids"] == ["run1"]
    assert "95% CI [-3.0, -1.0]" in row["summary"]
    assert "p=0.125" in row["summary"]
    assert row["payload"]["estimate"]["p_value"] == 0.125
    assert row["payload"]["estimate"]["p_value_method"] == "exact_label_permutation_two_sided"
