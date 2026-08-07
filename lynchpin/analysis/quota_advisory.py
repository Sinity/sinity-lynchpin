"""Asynchronous quota attribution and model-choice advice.

This module is deliberately outside the Sinnix request path.  It consumes
redacted quota snapshots and completed agent manifests, then joins them with
Polylogue token evidence and Beads outcomes.  Missing evidence stays missing.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from ..core.config import get_config
from ..core.io import save_json
from ..sources.polylogue import conversation_transcripts

FEATURES = (
    "uncached_input",
    "cache_read",
    "cache_write",
    "output",
    "reasoning",
    "tool_duration",
    "wall_time",
)
MIN_CONFIDENT_OBSERVATIONS = 4


@dataclass(frozen=True)
class QuotaObservation:
    provider: str
    account_hash: str | None
    plan_epoch: str | None
    workload_class: str
    bead_id: str | None
    job_id: str | None
    features: Mapping[str, float]
    quota_delta: float
    duration_seconds: float | None
    verified: bool | None
    window_reset: str | None = None


@dataclass(frozen=True)
class Calibration:
    provider: str
    account_hash: str | None
    plan_epoch: str | None
    observations: int
    covered_observations: int
    coefficients: Mapping[str, float]
    residual_rms: float | None
    residual_p95: float | None
    coverage_fraction: float
    confidence: str
    reason: str | None


def fit_calibration(
    observations: Sequence[QuotaObservation],
    *,
    min_observations: int = MIN_CONFIDENT_OBSERVATIONS,
) -> Calibration:
    """Fit a deterministic non-negative model using coordinate descent.

    Coordinate descent is sufficient here because the feature count is fixed
    and small.  The robust pass trims residuals above 3 median absolute
    deviations before the final fit, making one bad provider delta visible in
    residuals without allowing it to dominate coefficients.
    """
    if not observations:
        return Calibration("unknown", None, None, 0, 0, {}, None, None, 0.0, "none", "no observations")
    first = observations[0]
    covered = [o for o in observations if o.quota_delta >= 0 and any(o.features.get(f, 0) > 0 for f in FEATURES)]
    coverage = len(covered) / len(observations)
    if len(covered) < min_observations or coverage < 0.7:
        return Calibration(first.provider, first.account_hash, first.plan_epoch, len(observations), len(covered), {}, None, None, coverage, "low", "insufficient covered overlapping jobs")

    coefficients = _nnls(covered)
    residuals = [_predict(o.features, coefficients) - o.quota_delta for o in covered]
    center = median(residuals)
    deviations = [abs(r - center) for r in residuals]
    mad = median(deviations) or 0.0
    if mad > 0:
        trimmed = [o for o, r in zip(covered, residuals) if abs(r - center) <= 3 * mad]
        if len(trimmed) >= min_observations:
            coefficients = _nnls(trimmed)
            residuals = [_predict(o.features, coefficients) - o.quota_delta for o in trimmed]
            covered = trimmed
    absolute = sorted(abs(r) for r in residuals)
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    p95 = absolute[min(len(absolute) - 1, math.ceil(len(absolute) * 0.95) - 1)]
    confidence = "high" if len(covered) >= 8 and coverage >= 0.9 else "medium"
    return Calibration(first.provider, first.account_hash, first.plan_epoch, len(observations), len(covered), coefficients, rms, p95, coverage, confidence, None)


def build_advisory(
    observations: Sequence[QuotaObservation],
    *,
    quota_windows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build calibrations and ranked workload advice from joined evidence."""
    groups: dict[tuple[str, str | None, str | None], list[QuotaObservation]] = defaultdict(list)
    for observation in observations:
        groups[(observation.provider, observation.account_hash, observation.plan_epoch)].append(observation)
    calibrations = [fit_calibration(group) for _, group in sorted(groups.items())]
    advice: list[dict[str, Any]] = []
    for workload_class, plan_epoch in sorted({(o.workload_class, o.plan_epoch) for o in observations}):
        rows = [o for o in observations if o.workload_class == workload_class and o.plan_epoch == plan_epoch]
        calibration = next((c for c in calibrations if c.provider == rows[0].provider and c.plan_epoch == rows[0].plan_epoch), None)
        predicted = [_predict(o.features, calibration.coefficients) for o in rows] if calibration and calibration.coefficients else []
        verified = [o.verified for o in rows if o.verified is not None]
        advice.append({
            "workload_class": workload_class,
            "plan_epoch": plan_epoch,
            "bead_ids": sorted({o.bead_id for o in rows if o.bead_id}),
            "observations": len(rows),
            "expected_quota": round(sum(predicted) / len(predicted), 6) if predicted else None,
            "median_duration_seconds": median([o.duration_seconds for o in rows if o.duration_seconds is not None]) if any(o.duration_seconds is not None for o in rows) else None,
            "verification_rate": round(sum(1 for value in verified if value) / len(verified), 3) if verified else None,
            "binding_windows": sorted({str(w.get("resets_at")) for w in quota_windows if w.get("resets_at")}),
            "confidence": calibration.confidence if calibration else "none",
            "ranking_basis": ["quota", "duration", "verification", "binding_window"],
        })
    advice.sort(key=lambda row: (row["confidence"] == "none", -(row["verification_rate"] or 0), row["expected_quota"] is None, row["expected_quota"] or float("inf")))
    return {
        "schema": "lynchpin-quota-advisory-v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "calibrations": [_calibration_json(c) for c in calibrations],
        "advice": advice,
        "evidence": {"observations": len(observations), "quota_windows": len(quota_windows), "live_request_path": False},
    }


def write_quota_advisory(
    out_file: str | Path,
    *,
    observations: Sequence[QuotaObservation] | None = None,
    quota_windows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Materialize the product from configured live inputs or test rows."""
    rows = list(observations) if observations is not None else _load_live_observations()
    windows = list(quota_windows) if quota_windows is not None else _load_quota_windows()
    payload = build_advisory(rows, quota_windows=windows)
    payload["sources"] = {
        "quota": "redacted Sinnix quota rows",
        "agent_jobs": "v3 completion manifests",
        "polylogue": "session token evidence",
        "beads": "repository issue records",
    }
    save_json(out_file, payload, sort_keys=True)
    return payload


def _nnls(observations: Sequence[QuotaObservation]) -> dict[str, float]:
    beta = {feature: 0.0 for feature in FEATURES}
    for _ in range(1200):
        changed = 0.0
        for feature in FEATURES:
            denominator = sum(o.features.get(feature, 0.0) ** 2 for o in observations) + 1e-12
            numerator = sum(o.features.get(feature, 0.0) * (o.quota_delta - _predict(o.features, beta) + beta[feature] * o.features.get(feature, 0.0)) for o in observations)
            value = max(0.0, numerator / denominator)
            changed = max(changed, abs(value - beta[feature]))
            beta[feature] = value
        if changed < 1e-10:
            break
    return beta


def _predict(features: Mapping[str, float], coefficients: Mapping[str, float]) -> float:
    return sum(float(features.get(name, 0.0)) * float(coefficients.get(name, 0.0)) for name in FEATURES)


def _calibration_json(value: Calibration) -> dict[str, Any]:
    return {"provider": value.provider, "account_hash": value.account_hash, "plan_epoch": value.plan_epoch, "observations": value.observations, "covered_observations": value.covered_observations, "coverage_fraction": round(value.coverage_fraction, 4), "coefficients": dict(value.coefficients), "residual_rms": value.residual_rms, "residual_p95": value.residual_p95, "confidence": value.confidence, "reason": value.reason}


def _load_live_observations() -> list[QuotaObservation]:
    """Join currently available local artifacts without inventing missing data."""
    quota_rows = _load_quota_windows()
    jobs_root = Path(os.environ.get("SINNIX_AGENT_JOBS_ROOT", "/home/sinity/.local/state/sinnix/agent-gateway/jobs"))
    beads_path = get_config().sinnix_root / ".beads/issues.jsonl"
    beads = _load_jsonl(beads_path)
    bead_status = {str(row.get("id")): row.get("status") for row in beads if row.get("id")}
    profiles = _polylogue_token_index()
    observations: list[QuotaObservation] = []
    for path in sorted(jobs_root.glob("*.json")):
        job = _load_json(path)
        if not isinstance(job, dict) or job.get("lifecycle") != "completed":
            continue
        identity = job.get("identity") or {}
        completion = job.get("completion") or {}
        usage = job.get("quota_features") or job.get("usage") or {}
        bead_id = identity.get("bead_id") or (job.get("declared") or {}).get("work_item")
        provider = identity.get("provider") or job.get("backend") or "unknown"
        for row in quota_rows:
            if row.get("provider") != provider:
                continue
            features = {name: float(usage.get(name, 0) or 0) for name in FEATURES}
            token_data = profiles.get(identity.get("polylogue_session_id"), {})
            features["output"] = features["output"] or float(token_data.get("all_message_tokens", 0))
            observations.append(QuotaObservation(provider, identity.get("account_hash"), row.get("plan_epoch"), str((job.get("declared") or {}).get("role") or "unclassified"), bead_id if bead_id in bead_status else None, job.get("job_id"), features, float(row.get("delta_fraction", 0) or 0), _duration_seconds(job, completion), (bead_status.get(bead_id) == "closed") if bead_id else None, (row.get("window") or {}).get("resets_at")))
    return observations


def _load_quota_windows() -> list[dict[str, Any]]:
    path = Path(os.environ.get("SINNIX_QUOTA_NORMALIZED", "/run/user/1000/sinnix/quota/normalized.json"))
    payload = _load_json(path)
    rows = payload.get("rows", []) if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)]


def _polylogue_token_index() -> dict[str, dict[str, Any]]:
    try:
        end = datetime.now(timezone.utc).date()
        transcripts = conversation_transcripts(start=end, end=end)
    except Exception:
        return {}
    return {t.conversation_id: {"all_message_tokens": t.all_message_tokens} for t in transcripts}


def _duration_seconds(job: Mapping[str, Any], completion: Mapping[str, Any]) -> float | None:
    if completion.get("duration_seconds") is not None:
        return float(completion["duration_seconds"])
    if completion.get("duration_ms") is not None:
        return float(completion["duration_ms"]) / 1000
    return None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


__all__ = ["Calibration", "QuotaObservation", "build_advisory", "fit_calibration", "write_quota_advisory"]
