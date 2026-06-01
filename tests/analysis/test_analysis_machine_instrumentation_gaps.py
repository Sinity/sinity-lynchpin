from __future__ import annotations

from lynchpin.core.io import save_json


def test_machine_instrumentation_gaps_promote_support_assessment_gaps(tmp_path):
    from lynchpin.analysis.machine.instrumentation_gaps import analyze_machine_instrumentation_gaps

    support = tmp_path / "machine_support_assessment.json"
    save_json(
        support,
        {
            "assessments": [
                {
                    "assessment_id": "assess1",
                    "candidate_id": "cand1",
                    "project": "sinex",
                    "metric": "stage.duration_s",
                    "suspected_factor": "mined_cohort:stage=test",
                    "support_level": "insufficient",
                    "decision": "refuse_claim",
                    "source_artifacts": ["machine_mining.json"],
                    "refusal_reasons": ["no executed controlled benchmark claim exists"],
                    "mechanism": {
                        "mechanism_id": "machine-mechanism:stage_regression_or_workload_mix",
                        "mechanism_family": "stage_regression_or_workload_mix",
                        "current_support_ceiling": "candidate",
                    },
                    "instrumentation_gaps": [
                        {
                            "gap_id": "gap1",
                            "missing": "executed_controlled_run",
                            "why_it_matters": "no controlled estimate exists",
                            "next_action": "execute the approved manifest",
                        },
                        {
                            "gap_id": "gap2",
                            "missing": "complete_negative_controls",
                            "why_it_matters": "placebo check unavailable",
                            "next_action": "derive missing placebo check",
                        }
                    ],
                }
            ]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_instrumentation_gaps(support_assessment_path=support)

    assert analysis.gap_count == 2
    assert analysis.by_missing_source == {"controlled_benchmark_run": 1, "negative_control_check": 1}
    gap = next(row for row in analysis.gaps if row.gap_id == "gap1")
    assert gap.gap_id == "gap1"
    assert gap.assessment_id == "assess1"
    assert gap.candidate_id == "cand1"
    assert gap.project == "sinex"
    assert gap.missing_source == "controlled_benchmark_run"
    assert gap.missing_window == "planned_run_window"
    assert gap.support_blocked_at == "candidate"
    assert gap.source_artifacts == ("machine_mining.json",)
    negative = next(row for row in analysis.gaps if row.gap_id == "gap2")
    assert negative.missing_source == "negative_control_check"
    assert negative.missing_window == "matched_design_window"
