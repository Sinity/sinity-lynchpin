"""Generic calibration checks for evidence-shaped analysis claims."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from lynchpin.core.io import load_json_if_exists, save_json


@dataclass(frozen=True)
class ClaimCalibrationIssue:
    claim_id: str
    issue_type: str
    severity: str
    message: str


@dataclass(frozen=True)
class ClaimCalibrationReport:
    claim_count: int
    issue_count: int
    issue_counts: dict[str, int]
    support_counts: dict[str, int]
    evidence_backed_count: int
    issues: tuple[ClaimCalibrationIssue, ...]
    caveats: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def calibrate_claims(claims: Iterable[dict[str, Any]]) -> ClaimCalibrationReport:
    rows = list(claims)
    issues: list[ClaimCalibrationIssue] = []
    for row in rows:
        issues.extend(_claim_issues(row))
    issue_counts = Counter(issue.issue_type for issue in issues)
    support_counts = Counter(str(row.get("support_level") or "unknown") for row in rows)
    evidence_backed = sum(
        1
        for row in rows
        if row.get("source_ids") or row.get("relation_ids")
    )
    return ClaimCalibrationReport(
        claim_count=len(rows),
        issue_count=len(issues),
        issue_counts=dict(sorted(issue_counts.items())),
        support_counts=dict(sorted(support_counts.items())),
        evidence_backed_count=evidence_backed,
        issues=tuple(issues),
        caveats=(
            "calibration flags internal consistency only; it does not validate the underlying world model",
            "observational claims may be useful even when flagged for missing controlled support",
        ),
    )


def calibrate_claim_artifacts(paths: Iterable[str | Path]) -> ClaimCalibrationReport:
    claims: list[dict[str, Any]] = []
    for path in paths:
        payload = load_json_if_exists(path)
        if not isinstance(payload, dict):
            continue
        rows = payload.get("claims")
        if not isinstance(rows, list):
            continue
        claims.extend(row for row in rows if isinstance(row, dict))
    return calibrate_claims(claims)


def write_claim_calibration(
    out: Path,
    *,
    claim_artifacts: Iterable[str | Path],
) -> ClaimCalibrationReport:
    paths = tuple(claim_artifacts)
    report = calibrate_claim_artifacts(paths)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(paths),
        **report.to_json(),
    }
    save_json(out, payload, sort_keys=True)
    return report


def _claim_issues(row: dict[str, Any]) -> list[ClaimCalibrationIssue]:
    claim_id = str(row.get("claim_id") or "")
    support = str(row.get("support_level") or "").lower()
    confidence = _float(row.get("confidence")) or 0.0
    source_ids = tuple(row.get("source_ids") or ())
    relation_ids = tuple(row.get("relation_ids") or ())
    caveats = tuple(str(item).lower() for item in row.get("caveats") or ())
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    summary = str(row.get("summary") or "")
    issues: list[ClaimCalibrationIssue] = []
    if not source_ids and not relation_ids:
        issues.append(
            ClaimCalibrationIssue(
                claim_id=claim_id,
                issue_type="missing_evidence_ids",
                severity="medium",
                message="claim has no source_ids or relation_ids",
            )
        )
    if support == "strong" and confidence < 0.75:
        issues.append(
            ClaimCalibrationIssue(
                claim_id=claim_id,
                issue_type="support_confidence_mismatch",
                severity="high",
                message="strong support level has confidence below 0.75",
            )
        )
    if support in {"weak", "insufficient"} and confidence > 0.70:
        issues.append(
            ClaimCalibrationIssue(
                claim_id=claim_id,
                issue_type="support_confidence_mismatch",
                severity="medium",
                message="weak/insufficient support has high confidence",
            )
        )
    if _causal_language(summary, payload) and not _controlled_caveat(caveats, payload):
        issues.append(
            ClaimCalibrationIssue(
                claim_id=claim_id,
                issue_type="causal_language_without_control",
                severity="high",
                message="claim uses causal language without controlled/validated support markers",
            )
        )
    return issues


def _causal_language(summary: str, payload: dict[str, Any]) -> bool:
    text = f"{summary} {payload}".lower()
    return bool(
        re.search(r"\b(cause[sd]?|causal|effect|impact|attribut(?:e|ion)|due to)\b", text)
    )


def _controlled_caveat(caveats: tuple[str, ...], payload: dict[str, Any]) -> bool:
    text = " ".join((*caveats, str(payload))).lower()
    return any(marker in text for marker in ("controlled", "benchmark", "validated", "random"))


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


__all__ = [
    "ClaimCalibrationIssue",
    "ClaimCalibrationReport",
    "calibrate_claim_artifacts",
    "calibrate_claims",
    "write_claim_calibration",
]
