from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lynchpin.core.io import save_json


def test_machine_matched_designs_emit_controls_balance_and_placebo(tmp_path):
    from lynchpin.analysis.machine.matched_designs import analyze_machine_matched_designs

    frames = tmp_path / "machine_analysis_feature_frames.json"
    validation = tmp_path / "machine_validation_design.json"
    boundary_at = datetime(2026, 5, 2, 12, tzinfo=timezone.utc)
    rows = [
        *(_row(f"treated-before-{idx}", 10 + idx, "sinex", "test", "before", -120 + idx) for idx in range(4)),
        *(_row(f"treated-after-{idx}", 50 + idx, "sinex", "test", "after", idx) for idx in range(4)),
        *(_row(f"control-before-{idx}", 12 + idx, "sinex", "build", "other", -110 + idx) for idx in range(4)),
        *(_row(f"control-after-{idx}", 14 + idx, "sinex", "build", "other", 10 + idx) for idx in range(4)),
    ]
    save_json(
        frames,
        {"frame": {"outcome_metric": "stage.duration_s", "rows": rows}},
        sort_keys=True,
    )
    save_json(
        validation,
        {
            "boundaries": [{
                "boundary_id": "boundary1",
                "boundary_at": boundary_at.isoformat(),
                "dimensions": {
                    "project": "sinex",
                    "stage_name": "test",
                    "before_git_commit": "before",
                    "after_git_commit": "after",
                },
            }]
        },
        sort_keys=True,
    )

    analysis = analyze_machine_matched_designs(
        feature_frames_path=frames,
        validation_design_path=validation,
        min_side_rows=3,
    )

    same_project = next(row for row in analysis.designs if row.control_family == "same_project_other_stage")
    assert analysis.design_count == 2
    assert same_project.treated_delta == 40.0
    assert same_project.control_delta == 2.0
    assert same_project.difference_in_differences == 38.0
    assert same_project.placebo_delta == 2.0
    assert same_project.negative_control_status == "passed"
    assert same_project.identification_status == "design_ready"
    assert same_project.balance["treated_observed_n"] == 8


def _row(
    unit_id: str,
    outcome: float,
    project: str,
    stage: str,
    commit: str,
    offset_minutes: int,
) -> dict:
    started_at = datetime(2026, 5, 2, 12, tzinfo=timezone.utc)
    started_at = started_at.replace(minute=0) + timedelta(minutes=offset_minutes)
    return {
        "unit_id": unit_id,
        "outcome_window_start": started_at.isoformat(),
        "covariates": {
            "project": project,
            "stage_name": stage,
            "git_commit": commit,
            "cache_condition": "warm",
        },
        "outcome_value": outcome,
        "censoring_status": "observed",
    }
