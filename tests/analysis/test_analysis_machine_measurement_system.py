from __future__ import annotations

from lynchpin.core.io import save_json


def test_machine_measurement_system_summarizes_clock_censoring_and_repeatability(tmp_path):
    from lynchpin.analysis.machine.measurement_system import analyze_machine_measurement_system

    frames = tmp_path / "machine_analysis_feature_frames.json"
    save_json(
        frames,
        {"frame": {"row_count": 10, "censored_count": 2, "censoring_summary": {"observed": 8, "failed_or_cancelled": 2}}},
    )
    work = tmp_path / "machine_work_observations.json"
    save_json(
        work,
        {
            "stage_summaries": [
                {"stage_name": "a", "observation_count": 3, "median_duration_s": 1.0, "p95_duration_s": 2.0},
                {"stage_name": "b", "observation_count": 4, "median_duration_s": 2.0, "p95_duration_s": 3.0},
                {"stage_name": "c", "observation_count": 5, "median_duration_s": 3.0, "p95_duration_s": 4.0},
            ]
        },
    )
    experiments = tmp_path / "machine_experiment_claims.json"
    save_json(experiments, {"claim_packs": [], "effect_estimates": []})

    report = analyze_machine_measurement_system(
        feature_frames_path=frames,
        work_observations_path=work,
        experiments_path=experiments,
    )

    assert report.check_count == 5
    assert report.by_status["passed"] >= 3
    censoring = next(row for row in report.checks if row.check_kind == "censored_timeout_handling")
    assert censoring.status == "passed"
    assert censoring.evidence["censored_count"] == 2
    repeatability = next(row for row in report.checks if row.check_kind == "baseline_repeatability")
    assert repeatability.status == "passed"
    assert repeatability.evidence["repeated_stage_count"] == 3
