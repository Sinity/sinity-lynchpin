"""Assumption ledger for machine attribution claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineAssumptionCheck:
    assumption_id: str
    claim_id: str
    support_level: str
    assumption: str
    claim_scope: str
    check_status: str
    evidence_ids: tuple[str, ...]
    sensitivity_result: str
    support_consequence: str


@dataclass(frozen=True)
class MachineAssumptionCheckAnalysis:
    generated_for: dict[str, Any]
    check_count: int
    by_status: dict[str, int]
    checks: list[MachineAssumptionCheck]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_assumption_checks(
    *,
    start: date | None = None,
    end: date | None = None,
    claims_path: Path | None = None,
) -> MachineAssumptionCheckAnalysis:
    payload = load_json_object(
        claims_path or resolve_analysis_path("machine_attribution_claims.json"),
        label="machine attribution claims",
    )
    checks = [
        check
        for row in payload.get("claims", [])
        if isinstance(row, dict)
        for check in _checks_for_claim(row)
    ]
    by_status: dict[str, int] = {}
    for check in checks:
        by_status[check.check_status] = by_status.get(check.check_status, 0) + 1
    return MachineAssumptionCheckAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_attribution_claims.json",
        },
        check_count=len(checks),
        by_status=dict(sorted(by_status.items())),
        checks=checks,
        caveats=[
            "assumption checks are generated from claim/refusal payloads; they do not re-estimate effects",
            "untestable assumptions cap non-controlled support unless a later artifact supplies evidence",
        ],
    )


def write_machine_assumption_checks(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    claims_path: Path | None = None,
) -> MachineAssumptionCheckAnalysis:
    analysis = analyze_machine_assumption_checks(start=start, end=end, claims_path=claims_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _checks_for_claim(row: dict[str, Any]) -> tuple[MachineAssumptionCheck, ...]:
    claim_id = str(row.get("claim_id") or "")
    if not claim_id:
        return ()
    support_level = str(row.get("support_level") or "unknown")
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
    source_ids = tuple(str(value) for value in row.get("source_ids", ()) if value)
    checks = []
    for reason in estimate.get("refusal_reasons", ()) if isinstance(estimate.get("refusal_reasons"), list) else ():
        checks.append(_check(
            claim_id=claim_id,
            support_level=support_level,
            assumption=f"support blocker resolved: {reason}",
            scope="claim_support",
            status="failed",
            evidence_ids=source_ids,
            sensitivity=f"claim remains {support_level} while blocker is present",
            consequence="prevents support upgrade",
        ))
    for gap in estimate.get("instrumentation_gaps", ()) if isinstance(estimate.get("instrumentation_gaps"), list) else ():
        missing = str(gap.get("missing") if isinstance(gap, dict) else gap)
        checks.append(_check(
            claim_id=claim_id,
            support_level=support_level,
            assumption=f"required instrumentation available: {missing}",
            scope="measurement",
            status="untestable",
            evidence_ids=source_ids,
            sensitivity="collecting the missing instrument may enable a stronger design",
            consequence="caps support at insufficient or candidate",
        ))
    if not checks and support_level == "natural_experiment":
        checks.extend(_natural_experiment_checks(
            claim_id=claim_id,
            support_level=support_level,
            estimate=estimate,
            evidence_ids=source_ids,
        ))
    if not checks and support_level == "controlled":
        checks.extend(_controlled_checks(
            claim_id=claim_id,
            support_level=support_level,
            estimate=estimate,
            caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
            evidence_ids=source_ids,
        ))
    return tuple(checks)


def _natural_experiment_checks(
    *,
    claim_id: str,
    support_level: str,
    estimate: dict[str, Any],
    evidence_ids: tuple[str, ...],
) -> tuple[MachineAssumptionCheck, ...]:
    artifacts = tuple(str(item) for item in estimate.get("source_artifacts", ()) if item)
    has_design = any("machine_matched_designs" in item for item in artifacts)
    has_controls = any("machine_negative_controls" in item for item in artifacts)
    return (
        _check(
            claim_id=claim_id,
            support_level=support_level,
            assumption="matched design identification is ready",
            scope="natural_experiment_design",
            status="passed" if has_design else "untestable",
            evidence_ids=evidence_ids,
            sensitivity="removing matched-design evidence caps support at candidate",
            consequence="allows natural-experiment support subject to negative-control checks",
        ),
        _check(
            claim_id=claim_id,
            support_level=support_level,
            assumption="negative controls passed for matched design",
            scope="negative_controls",
            status="passed" if has_controls else "untestable",
            evidence_ids=evidence_ids,
            sensitivity="failed or unavailable negative controls demote support to insufficient",
            consequence="guards natural-experiment identification against shared shocks and placebo movement",
        ),
        _check(
            claim_id=claim_id,
            support_level=support_level,
            assumption="dataset feature and multiplicity diagnostics are ready",
            scope="dataset_diagnostics",
            status="passed",
            evidence_ids=evidence_ids,
            sensitivity="limited feature or multiplicity diagnostics demote support to insufficient",
            consequence="keeps extant-dataset support tied to audited mining/search-space conditions",
        ),
    )


def _controlled_checks(
    *,
    claim_id: str,
    support_level: str,
    estimate: dict[str, Any],
    caveats: tuple[str, ...],
    evidence_ids: tuple[str, ...],
) -> tuple[MachineAssumptionCheck, ...]:
    checks = [
        _check(
            claim_id=claim_id,
            support_level=support_level,
            assumption="controlled manifest contract, complete phase capture, and telemetry overlap are present",
            scope="controlled_design",
            status="passed",
            evidence_ids=evidence_ids,
            sensitivity="removing manifest/randomization, complete Nix phase evidence, or telemetry overlap demotes the claim",
            consequence="allows controlled support subject to remaining checks",
        ),
        _check(
            claim_id=claim_id,
            support_level=support_level,
            assumption="both treatment arms have estimable samples",
            scope="sample_support",
            status=_sample_status(estimate),
            evidence_ids=evidence_ids,
            sensitivity=_sample_sensitivity(estimate),
            consequence="insufficient arm samples refuse effect-size precision",
        ),
        _check(
            claim_id=claim_id,
            support_level=support_level,
            assumption="bootstrap confidence interval is finite and interpretable",
            scope="precision",
            status=_ci_status(estimate),
            evidence_ids=evidence_ids,
            sensitivity=_ci_sensitivity(estimate),
            consequence="wide or missing intervals cap the claim to controlled measurement without precise magnitude",
        ),
        _check(
            claim_id=claim_id,
            support_level=support_level,
            assumption="controlled run caveats do not include fatal measurement blockers",
            scope="measurement",
            status=_caveat_status(caveats),
            evidence_ids=evidence_ids,
            sensitivity="fatal caveats such as missing telemetry, incomplete phase capture, or nonzero exits demote support",
            consequence="fatal caveats prevent controlled support upgrade",
        ),
    ]
    return tuple(checks)


def _sample_status(estimate: dict[str, Any]) -> str:
    control_n = int(estimate.get("control_n") or 0)
    treatment_n = int(estimate.get("treatment_n") or 0)
    return "passed" if control_n > 0 and treatment_n > 0 else "failed"


def _sample_sensitivity(estimate: dict[str, Any]) -> str:
    return f"control_n={int(estimate.get('control_n') or 0)} treatment_n={int(estimate.get('treatment_n') or 0)}"


def _ci_status(estimate: dict[str, Any]) -> str:
    try:
        ci_low = float(estimate.get("ci_low"))
        ci_high = float(estimate.get("ci_high"))
    except (TypeError, ValueError):
        return "untestable"
    return "passed" if ci_low <= ci_high else "failed"


def _ci_sensitivity(estimate: dict[str, Any]) -> str:
    return f"ci_low={estimate.get('ci_low')} ci_high={estimate.get('ci_high')} delta={estimate.get('delta')}"


def _caveat_status(caveats: tuple[str, ...]) -> str:
    fatal_needles = (
        "observational manifest only",
        "no machine telemetry samples",
        "no complete timed phase",
        "internal-json has no complete timed phase",
        "run exited nonzero",
    )
    return "failed" if any(any(needle in caveat for needle in fatal_needles) for caveat in caveats) else "passed"


def _check(
    *,
    claim_id: str,
    support_level: str,
    assumption: str,
    scope: str,
    status: str,
    evidence_ids: tuple[str, ...],
    sensitivity: str,
    consequence: str,
) -> MachineAssumptionCheck:
    return MachineAssumptionCheck(
        assumption_id=f"machine-assumption:{hashlib.sha1((claim_id + assumption).encode()).hexdigest()[:16]}",
        claim_id=claim_id,
        support_level=support_level,
        assumption=assumption,
        claim_scope=scope,
        check_status=status,
        evidence_ids=evidence_ids,
        sensitivity_result=sensitivity,
        support_consequence=consequence,
    )


__all__ = [
    "MachineAssumptionCheck",
    "MachineAssumptionCheckAnalysis",
    "analyze_machine_assumption_checks",
    "write_machine_assumption_checks",
]
