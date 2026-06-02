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


def test_machine_attribution_claim_ids_include_source_evidence() -> None:
    from lynchpin.analysis.machine.attribution_claims import MachineAttributionClaim

    base = dict(
        claim_type="machine_attribution",
        project="sinex",
        date=None,
        metric="stage.duration_s",
        effect_kind="temporal_gap_boundary:test",
        support_level="natural_experiment",
        confidence=0.6,
        summary="Natural-experiment design support available",
        baseline={},
        comparison={},
        estimate={"effect_estimate": {"difference_in_differences": 9.0}},
        caveats=(),
    )

    left = MachineAttributionClaim(source_ids=("assessment:1", "design:1"), **base).to_analysis_claim()
    right = MachineAttributionClaim(source_ids=("assessment:2", "design:2"), **base).to_analysis_claim()

    assert left.claim_id != right.claim_id
    assert left.score == 9.0


def test_machine_attribution_claim_analysis_enriches_natural_experiment_estimates(tmp_path) -> None:
    from lynchpin.analysis.machine.attribution_claims import analyze_machine_attribution_claims
    from lynchpin.core.io import save_json

    support = tmp_path / "machine_support_assessment.json"
    experiments = tmp_path / "machine_experiment_claims.json"
    matched = tmp_path / "machine_matched_designs.json"
    negative = tmp_path / "machine_negative_controls.json"
    save_json(
        support,
        {
            "assessments": [{
                "assessment_id": "assess1",
                "candidate_id": "cand1",
                "project": "sinex",
                "metric": "stage.duration_s",
                "suspected_factor": "temporal_gap_boundary:test",
                "support_level": "natural_experiment",
                "confidence": 0.66,
                "decision": "promote_claim_candidate",
                "source_artifacts": ["machine_matched_designs.json", "machine_negative_controls.json"],
                "source_ids": ["design1", "boundary1"],
                "caveats": ["observational boundary"],
                "mechanism": {"suspected_driver": "cache_state"},
                "summary": "Natural-experiment design support available",
            }]
        },
        sort_keys=True,
    )
    save_json(experiments, {"effect_estimates": [], "claim_packs": []}, sort_keys=True)
    save_json(
        matched,
        {
            "designs": [{
                "design_id": "design1",
                "boundary_id": "boundary1",
                "boundary_type": "temporal_gap_boundary",
                "boundary_at": "2026-05-01T10:00:00+00:00",
                "project": "sinex",
                "stage_name": "test",
                "outcome_metric": "stage.duration_s",
                "treated_before_n": 4,
                "treated_after_n": 5,
                "treated_delta": 30.0,
                "control_before_n": 6,
                "control_after_n": 6,
                "control_delta": 7.0,
                "difference_in_differences": 23.0,
                "placebo_delta": 1.5,
                "balance": {"before_ratio": 0.9},
                "negative_control_status": "passed",
                "identification_status": "supportable",
                "support_ceiling": "natural_experiment",
                "caveats": ["non-randomized"],
            }]
        },
        sort_keys=True,
    )
    save_json(
        negative,
        {
            "controls": [{
                "control_id": "control1",
                "design_id": "design1",
                "control_kind": "placebo",
                "support_required": True,
                "status": "passed",
                "primary_delta": 30.0,
                "control_delta": 7.0,
                "placebo_delta": 1.5,
                "interpretation": "placebo stable",
                "support_consequence": "retain support",
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_attribution_claims(
        support_assessment_path=support,
        experiment_claims_path=experiments,
        matched_designs_path=matched,
        negative_controls_path=negative,
    )

    row = analysis.claims[0]
    estimate = row["payload"]["estimate"]
    assert row["score"] == 23.0
    assert estimate["estimator"] == "matched median difference-in-differences"
    assert estimate["interval_status"] == "not_estimated_for_natural_experiment"
    assert estimate["boundary"]["boundary_id"] == "boundary1"
    assert estimate["sample_counts"] == {
        "treated_before_n": 4,
        "treated_after_n": 5,
        "control_before_n": 6,
        "control_after_n": 6,
    }
    assert estimate["effect_estimate"]["difference_in_differences"] == 23.0
    assert estimate["negative_control_status"] == "passed"
    assert estimate["negative_controls"][0]["interpretation"] == "placebo stable"
    assert estimate["negative_control_sensitivity"]["passed_count"] == 1
    assert estimate["assumption_ledger"]["checked_caveats"] == [
        "observational boundary",
        "non-randomized",
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
