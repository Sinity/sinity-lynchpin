from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.core.io import save_json


def test_machine_dataset_diagnostics_audit_extant_mining_dataset(tmp_path):
    from lynchpin.analysis.machine.dataset_diagnostics import analyze_machine_dataset_diagnostics

    frames = tmp_path / "machine_analysis_feature_frames.json"
    mining = tmp_path / "machine_mining.json"
    save_json(
        frames,
        {
            "frame": {
                "frame_id": "frame1",
                "unit_type": "work_observation_stage",
                "outcome_metric": "stage.duration_s",
                "rows": [
                    _row("r1", 1, missing=True),
                    _row("r2", 1),
                    _row("r3", 2),
                    _row("r4", 2, censored=True),
                    _row("r5", 3, leakage="invalid_negative_window"),
                ],
            }
        },
        sort_keys=True,
    )
    save_json(
        mining,
        {
            "scan_count": 1,
            "scan": {
                "scan_id": "scan1",
                "dimensions": ["stage_name", "project"],
                "comparison_universe_size": 4,
                "emitted_candidate_count": 2,
                "multiplicity_policy": "registered denominator with FDR gate",
            },
            "cohort_count": 2,
            "lagged_exposure_count": 0,
        },
        sort_keys=True,
    )

    analysis = analyze_machine_dataset_diagnostics(
        feature_frames_path=frames,
        mining_path=mining,
        min_fold_rows=2,
    )

    assert analysis.feature_audit.row_count == 5
    assert analysis.feature_audit.observed_count == 4
    assert analysis.feature_audit.censored_count == 1
    assert analysis.feature_audit.temporal_fold_count == 2
    assert analysis.feature_audit.status == "limited"
    assert analysis.feature_audit.top_missingness[0] == {"field": "host", "missing_count": 1}
    assert analysis.feature_audit.missingness_by_field["host"] == 1
    assert analysis.mining_audit.multiplicity_status == "registered"
    assert analysis.mining_audit.candidate_ratio == 0.5
    assert {row.diagnostic_kind for row in analysis.diagnostics} >= {
        "feature_frame_coverage",
        "search_space_registration",
        "missingness",
    }


def test_machine_dataset_diagnostics_explain_empty_lagged_pressure(tmp_path):
    from lynchpin.analysis.machine.dataset_diagnostics import analyze_machine_dataset_diagnostics

    frames = tmp_path / "machine_analysis_feature_frames.json"
    mining = tmp_path / "machine_mining.json"
    save_json(
        frames,
        {
            "frame": {
                "frame_id": "frame1",
                "unit_type": "work_observation_stage",
                "outcome_metric": "stage.duration_s",
                "rows": [
                    _row("r1", 1, pressure_missing=True),
                    _row("r2", 1, pressure_missing=True),
                    _row("r3", 2, pressure_missing=True),
                    _row("r4", 2, pressure_missing=True),
                ],
            }
        },
        sort_keys=True,
    )
    save_json(
        mining,
        {
            "scan_count": 1,
            "scan": {
                "comparison_universe_size": 1,
                "emitted_candidate_count": 1,
                "multiplicity_policy": "registered",
            },
            "lagged_exposure_count": 0,
        },
        sort_keys=True,
    )

    analysis = analyze_machine_dataset_diagnostics(
        feature_frames_path=frames,
        mining_path=mining,
        min_fold_rows=2,
    )

    diagnostic = next(row for row in analysis.diagnostics if row.diagnostic_kind == "pressure_lag_unavailable")
    assert diagnostic.status == "blocked_by_missing_pressure_covariates"
    assert diagnostic.evidence == ("host_io_pressure_some_avg10_max=4/4",)
    assert "pressure covariates" in diagnostic.next_action


def _row(
    unit_id: str,
    day: int,
    *,
    missing: bool = False,
    pressure_missing: bool = False,
    censored: bool = False,
    leakage: str = "ok",
) -> dict:
    ts = datetime(2026, 5, day, 12, tzinfo=timezone.utc).isoformat()
    return {
        "unit_id": unit_id,
        "outcome_value": 1.0,
        "outcome_window_start": ts,
        "covariates": {"stage_name": "test", "project": "sinex"},
        "missingness": {
            "host": missing,
            "host_io_pressure_some_avg10_max": pressure_missing,
        },
        "censoring_status": "failed_or_cancelled" if censored else "observed",
        "leakage_status": leakage,
    }
