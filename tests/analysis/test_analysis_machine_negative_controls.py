from __future__ import annotations

from lynchpin.core.io import save_json


def test_machine_negative_controls_expand_matched_design_checks(tmp_path):
    from lynchpin.analysis.machine.negative_controls import analyze_machine_negative_controls

    designs = tmp_path / "machine_matched_designs.json"
    save_json(
        designs,
        {
            "designs": [{
                "design_id": "design1",
                "boundary_id": "boundary1",
                "project": "sinex",
                "stage_name": "test",
                "treated_delta": 40.0,
                "control_family": "same_project_other_stage",
                "control_delta": 2.0,
                "placebo_delta": 2.0,
                "negative_control_status": "passed",
                "caveats": ["not randomized"],
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_negative_controls(matched_designs_path=designs)

    assert analysis.control_count == 2
    assert analysis.by_status == {"passed": 2}
    kinds = {row.control_kind for row in analysis.controls}
    assert kinds == {"same_project_other_stage", "pre_boundary_placebo"}
    assert {row.control_kind for row in analysis.controls if row.support_required} == {"same_project_other_stage"}
    assert all(row.project == "sinex" for row in analysis.controls)


def test_machine_negative_controls_fail_large_placebo(tmp_path):
    from lynchpin.analysis.machine.negative_controls import analyze_machine_negative_controls

    designs = tmp_path / "machine_matched_designs.json"
    save_json(
        designs,
        {
            "designs": [{
                "design_id": "design1",
                "boundary_id": "boundary1",
                "treated_delta": 10.0,
                "control_delta": 1.0,
                "placebo_delta": 8.0,
                "negative_control_status": "passed",
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_negative_controls(matched_designs_path=designs)

    assert analysis.by_status == {"failed": 1, "passed": 1}
    failed = next(row for row in analysis.controls if row.status == "failed")
    assert failed.control_kind == "pre_boundary_placebo"
    assert failed.support_required is False
    assert "blocks natural-experiment support" in failed.support_consequence
