from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.core.io import save_json


def test_validation_design_splits_temporally_and_finds_git_boundaries(tmp_path):
    from lynchpin.analysis.machine.validation_design import analyze_machine_validation_design

    frames = tmp_path / "machine_analysis_feature_frames.json"
    rows = [
        *(_row(f"a-{idx}", "a", idx, 10.0 + idx) for idx in range(4)),
        *(_row(f"b-{idx}", "b", idx + 4, 30.0 + idx) for idx in range(4)),
    ]
    save_json(
        frames,
        {"frame": {"unit_type": "work_observation_stage", "rows": rows}},
        sort_keys=True,
    )

    analysis = analyze_machine_validation_design(
        feature_frames_path=frames,
        split_fraction=0.5,
        min_boundary_rows=3,
    )

    assert analysis.split.discovery_row_count == 4
    assert analysis.split.validation_row_count == 4
    assert analysis.split.leakage_status == "ok"
    assert analysis.boundary_count == 1
    boundary = analysis.boundaries[0]
    assert boundary.boundary_type == "git_commit_transition"
    assert boundary.dimensions["before_git_commit"] == "a"
    assert boundary.dimensions["after_git_commit"] == "b"
    assert boundary.support_ceiling == "natural_experiment"
    assert boundary.median_delta > 15


def _row(unit_id: str, commit: str, minute: int, outcome: float) -> dict:
    ts = datetime(2026, 5, 1, 12, minute, tzinfo=timezone.utc).isoformat()
    return {
        "unit_id": unit_id,
        "outcome_window_start": ts,
        "outcome_value": outcome,
        "censoring_status": "observed",
        "project": "sinex",
        "covariates": {
            "project": "sinex",
            "stage_name": "test",
            "git_commit": commit,
        },
    }
