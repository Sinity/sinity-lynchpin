from __future__ import annotations

import pytest

from lynchpin.analysis.quota_advisory import QuotaObservation, build_advisory, fit_calibration


def _obs(value: float, *, x: float, workload: str = "implementation", epoch: str = "epoch-a", verified: bool = True) -> QuotaObservation:
    return QuotaObservation("codex", "sha256:account", epoch, workload, "sinnix-x", "job", {"uncached_input": x}, value, 10.0, verified)


def test_overlapping_jobs_recover_non_negative_coefficient() -> None:
    rows = [_obs(2 * x, x=x) for x in (1, 2, 3, 4, 5)]
    result = fit_calibration(rows)
    assert result.confidence == "medium"
    assert result.coefficients["uncached_input"] == pytest.approx(2.0)
    assert result.residual_rms == pytest.approx(0.0)


def test_plan_epochs_are_separate_and_advice_exposes_quality() -> None:
    rows = [_obs(2 * x, x=x, epoch="epoch-a") for x in (1, 2, 3, 4)]
    rows += [_obs(4 * x, x=x, epoch="epoch-b", verified=False) for x in (1, 2, 3, 4)]
    payload = build_advisory(rows, quota_windows=[{"resets_at": "2026-08-08T00:00:00Z"}])
    assert {row["plan_epoch"] for row in payload["calibrations"]} == {"epoch-a", "epoch-b"}
    advice = payload["advice"][0]
    assert advice["verification_rate"] in {0.0, 1.0}
    assert advice["ranking_basis"] == ["quota", "duration", "verification", "binding_window"]
    assert advice["binding_windows"] == ["2026-08-08T00:00:00Z"]


def test_low_coverage_does_not_publish_confident_advice() -> None:
    rows = [_obs(2, x=1), QuotaObservation("codex", "sha256:account", "epoch-a", "implementation", "sinnix-x", "job-2", {}, 1.0, 10.0, True)]
    result = fit_calibration(rows)
    assert result.confidence == "low"
    assert result.coefficients == {}
    assert result.reason is not None
