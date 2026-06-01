from __future__ import annotations

from lynchpin.core.io import save_json


def test_support_assessment_refuses_candidate_without_ready_plan_or_claim(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    claims = tmp_path / "machine_experiment_claims.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    save_json(candidates, {"candidates": [_candidate()]}, sort_keys=True)
    save_json(
        plans,
        {
            "plans": [{
                "candidate_id": "ignored",
                "planning_status": "needs_binding",
                "required_bindings": ["fixed_derivation_set"],
                "readiness": {"controlled": False, "issues": ["missing fixed derivation set"]},
                "manifest_preview": {"candidate": {"candidate_id": "cand1"}},
            }]
        },
        sort_keys=True,
    )
    save_json(bundle, {"run_template_count": 0}, sort_keys=True)
    save_json(claims, {"controlled_claim_count": 0}, sort_keys=True)

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
    )

    assert analysis.assessment_count == 1
    assert analysis.refusal_count == 1
    row = analysis.assessments[0]
    assert row.support_level == "insufficient"
    assert row.decision == "refuse_claim"
    assert row.mechanism.mechanism_family == "stage_regression_or_workload_mix"
    assert "no executed controlled benchmark claim exists" in row.refusal_reasons
    assert "fixed_derivation_set" in {gap.missing for gap in row.instrumentation_gaps}


def test_support_assessment_names_ready_infra_without_executed_claim(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    save_json(candidates, {"candidates": [_candidate()]}, sort_keys=True)
    save_json(
        plans,
        {
            "ready_plan_count": 1,
            "plans": [{
                "planning_status": "ready",
                "required_bindings": [],
                "readiness": {"controlled": True, "issues": []},
                "manifest_preview": {"candidate": {"candidate_id": "cand1"}},
            }],
        },
        sort_keys=True,
    )
    save_json(bundle, {"run_template_count": 12}, sort_keys=True)
    save_json(claims, {"controlled_claim_count": 0}, sort_keys=True)

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
    )

    assert analysis.ready_plan_count == 1
    assert analysis.run_template_count == 12
    row = analysis.assessments[0]
    assert row.support_level == "insufficient"
    assert "ready benchmark manifest templates exist but no executed controlled benchmark claim exists for candidate" in row.refusal_reasons
    assert {gap.missing for gap in row.instrumentation_gaps} == {"executed_controlled_run"}


def test_support_assessment_does_not_reuse_unrelated_controlled_claim(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    save_json(candidates, {"candidates": [_candidate()]}, sort_keys=True)
    save_json(
        plans,
        {
            "ready_plan_count": 1,
            "plans": [{
                "planning_status": "ready",
                "required_bindings": [],
                "readiness": {"controlled": True, "issues": []},
                "manifest_preview": {
                    "candidate": {"candidate_id": "cand1"},
                    "controlled_benchmark": {"run_group_id": "candidate-group"},
                },
            }],
        },
        sort_keys=True,
    )
    save_json(bundle, {"run_template_count": 12}, sort_keys=True)
    save_json(
        claims,
        {
            "controlled_claim_count": 1,
            "claim_packs": [{"claim_mode": "controlled_benchmark", "run_group_id": "other-group"}],
        },
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
    )

    row = analysis.assessments[0]
    assert row.support_level == "insufficient"
    assert "ready benchmark manifest templates exist but no executed controlled benchmark claim exists for candidate" in row.refusal_reasons


def test_support_assessment_accepts_matching_controlled_run_group(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    save_json(candidates, {"candidates": [{**_candidate(), "support_ceiling": "controlled"}]}, sort_keys=True)
    save_json(
        plans,
        {
            "ready_plan_count": 1,
            "plans": [{
                "planning_status": "ready",
                "required_bindings": [],
                "readiness": {"controlled": True, "issues": []},
                "manifest_preview": {
                    "candidate": {"candidate_id": "cand1"},
                    "controlled_benchmark": {"run_group_id": "candidate-group"},
                },
            }],
        },
        sort_keys=True,
    )
    save_json(bundle, {"run_template_count": 12}, sort_keys=True)
    save_json(
        claims,
        {
            "controlled_claim_count": 1,
            "claim_packs": [{"claim_mode": "controlled_benchmark", "run_group_id": "candidate-group"}],
        },
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
    )

    row = analysis.assessments[0]
    assert row.support_level == "controlled"
    assert row.refusal_reasons == ()


def test_support_assessment_controlled_run_overrides_source_candidate_ceiling(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    save_json(candidates, {"candidates": [{**_candidate(), "support_ceiling": "candidate"}]}, sort_keys=True)
    save_json(
        plans,
        {
            "ready_plan_count": 1,
            "plans": [{
                "planning_status": "ready",
                "required_bindings": [],
                "readiness": {"controlled": True, "issues": []},
                "manifest_preview": {
                    "candidate": {"candidate_id": "cand1"},
                    "controlled_benchmark": {"run_group_id": "candidate-group"},
                },
            }],
        },
        sort_keys=True,
    )
    save_json(bundle, {"run_template_count": 12}, sort_keys=True)
    save_json(
        claims,
        {
            "controlled_claim_count": 1,
            "claim_packs": [{"claim_mode": "controlled_benchmark", "run_group_id": "candidate-group"}],
        },
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
    )

    row = analysis.assessments[0]
    assert row.support_level == "controlled"
    assert "source candidate support ceiling is candidate" not in row.refusal_reasons


def test_support_assessment_accepts_ready_natural_experiment_without_controlled_run(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    diagnostics = tmp_path / "machine_dataset_diagnostics.json"
    matched = tmp_path / "machine_matched_designs.json"
    controls = tmp_path / "machine_negative_controls.json"
    save_json(candidates, {"candidates": [_natural_candidate()]}, sort_keys=True)
    save_json(plans, {"ready_plan_count": 0, "plans": []}, sort_keys=True)
    save_json(bundle, {"run_template_count": 0}, sort_keys=True)
    save_json(claims, {"controlled_claim_count": 0, "claim_packs": []}, sort_keys=True)
    save_json(diagnostics, _ready_dataset(), sort_keys=True)
    save_json(matched, {"designs": [_matched_design()]}, sort_keys=True)
    save_json(
        controls,
        {
            "controls": [
                {"design_id": "design1", "control_id": "nc1", "status": "passed"},
                {"design_id": "design1", "control_id": "nc2", "status": "passed"},
            ]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
        dataset_diagnostics_path=diagnostics,
        matched_designs_path=matched,
        negative_controls_path=controls,
    )

    assert analysis.controlled_claim_count == 0
    assert analysis.natural_experiment_support_count == 1
    row = analysis.assessments[0]
    assert row.support_level == "natural_experiment"
    assert row.refusal_reasons == ()
    assert row.instrumentation_gaps == ()
    assert "machine_negative_controls.json" in row.source_artifacts
    assert "design1" in row.source_ids


def test_support_assessment_refuses_natural_experiment_when_negative_control_fails(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    diagnostics = tmp_path / "machine_dataset_diagnostics.json"
    matched = tmp_path / "machine_matched_designs.json"
    controls = tmp_path / "machine_negative_controls.json"
    save_json(candidates, {"candidates": [_natural_candidate()]}, sort_keys=True)
    save_json(plans, {"ready_plan_count": 0, "plans": []}, sort_keys=True)
    save_json(bundle, {"run_template_count": 0}, sort_keys=True)
    save_json(claims, {"controlled_claim_count": 0, "claim_packs": []}, sort_keys=True)
    save_json(diagnostics, _ready_dataset(), sort_keys=True)
    save_json(matched, {"designs": [_matched_design()]}, sort_keys=True)
    save_json(
        controls,
        {"controls": [{"design_id": "design1", "control_id": "nc1", "status": "failed"}]},
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
        dataset_diagnostics_path=diagnostics,
        matched_designs_path=matched,
        negative_controls_path=controls,
    )

    row = analysis.assessments[0]
    assert row.support_level == "insufficient"
    assert "one or more negative-control checks failed for matched design" in row.refusal_reasons
    assert "no executed controlled benchmark claim exists" not in row.refusal_reasons
    assert "resolved_negative_controls" in {gap.missing for gap in row.instrumentation_gaps}


def test_support_assessment_allows_unavailable_diagnostic_placebo_control(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    diagnostics = tmp_path / "machine_dataset_diagnostics.json"
    matched = tmp_path / "machine_matched_designs.json"
    controls = tmp_path / "machine_negative_controls.json"
    save_json(candidates, {"candidates": [_natural_candidate()]}, sort_keys=True)
    save_json(plans, {"ready_plan_count": 0, "plans": []}, sort_keys=True)
    save_json(bundle, {"run_template_count": 0}, sort_keys=True)
    save_json(claims, {"controlled_claim_count": 0, "claim_packs": []}, sort_keys=True)
    save_json(diagnostics, _ready_dataset(), sort_keys=True)
    save_json(matched, {"designs": [_matched_design()]}, sort_keys=True)
    save_json(
        controls,
        {
            "controls": [
                {"design_id": "design1", "control_id": "required", "status": "passed", "support_required": True},
                {
                    "design_id": "design1",
                    "control_id": "diagnostic-placebo",
                    "status": "unavailable",
                    "support_required": False,
                },
            ]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
        dataset_diagnostics_path=diagnostics,
        matched_designs_path=matched,
        negative_controls_path=controls,
    )

    row = analysis.assessments[0]
    assert row.support_level == "natural_experiment"
    assert "complete_negative_controls" not in {gap.missing for gap in row.instrumentation_gaps}


def test_support_assessment_preserves_candidate_queue_order_before_limit(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    diagnostics = tmp_path / "machine_dataset_diagnostics.json"
    matched = tmp_path / "machine_matched_designs.json"
    controls = tmp_path / "machine_negative_controls.json"
    save_json(
        candidates,
        {
            "candidates": [
                {**_natural_candidate(), "priority_score": 1.0},
                {**_candidate(), "candidate_id": "huge", "priority_score": 1_000_000.0},
            ]
        },
        sort_keys=True,
    )
    save_json(plans, {"ready_plan_count": 0, "plans": []}, sort_keys=True)
    save_json(bundle, {"run_template_count": 0}, sort_keys=True)
    save_json(claims, {"controlled_claim_count": 0, "claim_packs": []}, sort_keys=True)
    save_json(diagnostics, _ready_dataset(), sort_keys=True)
    save_json(matched, {"designs": [_matched_design()]}, sort_keys=True)
    save_json(
        controls,
        {"controls": [{"design_id": "design1", "control_id": "nc1", "status": "passed"}]},
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
        dataset_diagnostics_path=diagnostics,
        matched_designs_path=matched,
        negative_controls_path=controls,
        limit=1,
    )

    assert analysis.assessments[0].candidate_id == "nat1"


def test_support_assessment_consumes_dataset_diagnostics(tmp_path):
    from lynchpin.analysis.machine.support_assessment import analyze_machine_support_assessment

    candidates = tmp_path / "machine_attribution_candidates.json"
    plans = tmp_path / "machine_benchmark_plans.json"
    bundle = tmp_path / "machine_benchmark_manifest_bundle.json"
    claims = tmp_path / "machine_experiment_claims.json"
    diagnostics = tmp_path / "machine_dataset_diagnostics.json"
    save_json(candidates, {"candidates": [_candidate()]}, sort_keys=True)
    save_json(
        plans,
        {
            "ready_plan_count": 1,
            "plans": [{
                "planning_status": "ready",
                "required_bindings": [],
                "readiness": {"controlled": True, "issues": []},
                "manifest_preview": {"candidate": {"candidate_id": "cand1"}},
            }],
        },
        sort_keys=True,
    )
    save_json(bundle, {"run_template_count": 12}, sort_keys=True)
    save_json(claims, {"controlled_claim_count": 0}, sort_keys=True)
    save_json(
        diagnostics,
        {
            "feature_audit": {"status": "limited"},
            "mining_audit": {"multiplicity_status": "limited"},
        },
        sort_keys=True,
    )

    analysis = analyze_machine_support_assessment(
        candidates_path=candidates,
        plans_path=plans,
        manifest_bundle_path=bundle,
        claims_path=claims,
        dataset_diagnostics_path=diagnostics,
    )

    row = analysis.assessments[0]
    assert analysis.dataset_feature_status == "limited"
    assert analysis.dataset_multiplicity_status == "limited"
    assert "extant dataset feature audit is limited" in row.refusal_reasons
    assert "extant dataset search-space audit is limited" in row.refusal_reasons


def _candidate() -> dict:
    return {
        "candidate_id": "cand1",
        "project": "sinex",
        "metric": "stage.duration_s",
        "suspected_factor": "cohort_contrast:stage=test",
        "mechanism_family": "observational_stage_contrast",
        "support_ceiling": "candidate",
        "priority_score": 10.0,
        "source_artifacts": ["machine_comparisons.json"],
        "source_ids": ["contrast1"],
        "caveats": ["observational"],
    }


def _natural_candidate() -> dict:
    return {
        **_candidate(),
        "candidate_id": "nat1",
        "mechanism_family": "natural_experiment_boundary",
        "support_ceiling": "natural_experiment_design",
        "source_artifacts": ["machine_matched_designs.json"],
        "source_ids": ["design1", "boundary1"],
    }


def _matched_design() -> dict:
    return {
        "design_id": "design1",
        "boundary_id": "boundary1",
        "project": "sinex",
        "stage_name": "test",
        "identification_status": "design_ready",
        "support_ceiling": "natural_experiment_design",
        "difference_in_differences": 12.5,
    }


def _ready_dataset() -> dict:
    return {
        "feature_audit": {"status": "ready_for_mining"},
        "mining_audit": {"multiplicity_status": "registered"},
    }
