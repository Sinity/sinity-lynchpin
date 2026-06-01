from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_machine_mining_records_scan_denominator_and_cohorts(tmp_path):
    from lynchpin.analysis.machine.mining import analyze_machine_mining
    from lynchpin.core.io import save_json

    frames = tmp_path / "machine_analysis_feature_frames.json"
    save_json(
        frames,
        {
            "frame": {
                "frame_id": "frame1",
                "unit_type": "work_observation_stage",
                "outcome_metric": "stage.duration_s",
                "rows": [
                    _row("r1", "nextest", "sinex", 10.0),
                    _row("r2", "nextest", "sinex", 20.0, missing=True, pressure=0.8, minute=10),
                    _row("r6", "nextest", "sinex", 35.0, pressure=0.2, minute=20),
                    _row("r3", "build", "sinex", 5.0),
                    _row("r4", "build", "sinex", 7.0, censored=True),
                    _row("r5", "lint", "lynchpin", 1.0),
                ],
            },
        },
        sort_keys=True,
    )

    analysis = analyze_machine_mining(
        feature_frames_path=frames,
        dimensions=("stage_name", "project"),
        min_cohort_size=2,
    )

    assert analysis.scan.row_count == 6
    assert analysis.scan_count == 1
    assert analysis.scans == [analysis.scan]
    assert analysis.scan.comparison_universe_size == 3
    assert analysis.scan.emitted_candidate_count == 2
    assert "denominator" in analysis.scan.multiplicity_policy
    assert analysis.cohort_count == 2
    top = analysis.cohorts[0]
    assert top.dimensions == {"stage_name": "nextest", "project": "sinex"}
    assert top.row_count == 3
    assert top.median_outcome == 20.0
    assert top.missing_value_count == 1
    assert top.leakage_status == "ok"
    assert analysis.lagged_exposure_count == 1
    lagged = analysis.lagged_exposures[0]
    assert lagged.pressure_metric == "host_io_pressure_some_avg10_max"
    assert lagged.high_prior_pressure_count == 1
    assert lagged.median_outcome_after_prior_pressure == 35.0
    assert analysis.anomaly_cluster_count == 1
    cluster = analysis.anomaly_clusters[0]
    assert cluster.dimensions == {"stage_name": "nextest", "project": "sinex"}
    assert cluster.representative_unit_ids == ("r2", "r6")
    assert "exploratory" in analysis.caveats[0]


def _row(
    unit_id: str,
    stage: str,
    project: str,
    outcome: float,
    *,
    missing: bool = False,
    censored: bool = False,
    pressure: float | None = None,
    minute: int = 0,
) -> dict:
    start = datetime(2026, 5, 31, 12, tzinfo=timezone.utc) + timedelta(minutes=minute)
    end = start + timedelta(minutes=1)
    return {
        "frame_id": unit_id,
        "unit_type": "work_observation_stage",
        "unit_id": unit_id,
        "parent_unit_id": f"parent-{unit_id}",
        "project": project,
        "outcome_metric": "stage.duration_s",
        "outcome_value": outcome,
        "outcome_window_start": start.isoformat(),
        "outcome_window_end": end.isoformat(),
        "exposure_window_start": start.isoformat(),
        "exposure_window_end": end.isoformat(),
        "exposure_policy": "concurrent_context",
        "covariates": {
            "stage_name": stage,
            "project": project,
            "host_io_pressure_some_avg10_max": pressure,
        },
        "missingness": {"host_cpu_pressure_some_avg10_max": missing},
        "censoring_status": "failed_or_cancelled" if censored else "observed",
        "leakage_status": "ok",
        "caveats": [],
    }
