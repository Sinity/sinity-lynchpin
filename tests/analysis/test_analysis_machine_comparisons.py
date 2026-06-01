from __future__ import annotations

from lynchpin.core.io import save_json


def test_machine_comparisons_estimate_contrasts_with_q_values(tmp_path):
    from lynchpin.analysis.machine.comparisons import analyze_machine_comparisons

    frames = tmp_path / "machine_analysis_feature_frames.json"
    mining = tmp_path / "machine_mining.json"
    rows = [
        *(_row(f"slow-{idx}", "test", "sinex", 100.0 + idx) for idx in range(8)),
        *(_row(f"fast-{idx}", "build", "sinex", 10.0 + idx) for idx in range(8)),
        _row("censored", "test", "sinex", 500.0, censored=True),
    ]
    save_json(
        frames,
        {"frame": {"frame_id": "frame1", "unit_type": "work_observation_stage", "outcome_metric": "stage.duration_s", "rows": rows}},
        sort_keys=True,
    )
    save_json(
        mining,
        {
            "scan": {"scan_id": "scan1", "dimensions": ["stage_name", "project"]},
            "cohorts": [
                {"cohort_id": "cohort-slow", "scan_id": "scan1", "dimensions": {"stage_name": "test", "project": "sinex"}},
                {"cohort_id": "cohort-fast", "scan_id": "scan1", "dimensions": {"stage_name": "build", "project": "sinex"}},
            ],
        },
        sort_keys=True,
    )

    analysis = analyze_machine_comparisons(
        feature_frames_path=frames,
        mining_path=mining,
        bootstrap_iterations=100,
    )

    assert analysis.contrast_count == 2
    slow = next(row for row in analysis.contrasts if row.cohort_id == "cohort-slow")
    assert slow.treated_n == 8
    assert slow.comparison_n == 8
    assert slow.median_delta > 80
    assert slow.bootstrap_ci_95[0] > 0
    assert slow.mann_whitney_p is not None
    assert slow.q_value is not None
    assert slow.statistical_signal == "screening_signal"
    assert slow.support_ceiling == "candidate"


def _row(unit_id: str, stage: str, project: str, outcome: float, *, censored: bool = False) -> dict:
    return {
        "unit_id": unit_id,
        "covariates": {"stage_name": stage, "project": project},
        "outcome_value": outcome,
        "censoring_status": "failed_or_cancelled" if censored else "observed",
    }
