"""Typed machine attribution claims backed by the generic claim substrate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

from lynchpin.core.io import load_json_if_exists, resolve_analysis_path, save_json
from lynchpin.core.parse import parse_datetime
from lynchpin.core.projects import canonical_project_name
from lynchpin.substrate.claims import AnalysisClaimRow, claim_id

SupportLevel = Literal[
    "controlled",
    "natural_experiment",
    "observational",
    "insufficient",
]


@dataclass(frozen=True)
class MachineAttributionClaim:
    claim_type: str
    project: str | None
    date: date | None
    metric: str
    effect_kind: str
    support_level: SupportLevel
    confidence: float
    summary: str
    baseline: dict[str, Any]
    comparison: dict[str, Any]
    estimate: dict[str, Any]
    source_ids: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def to_analysis_claim(self) -> AnalysisClaimRow:
        _validate_confidence(self.confidence)
        score = _estimate_score(self.estimate)
        return AnalysisClaimRow(
            claim_id=claim_id(
                "machine_attribution",
                self.project,
                self.date,
                self.metric,
                self.effect_kind,
                self.summary,
            ),
            claim_type=self.claim_type,
            project=self.project,
            date=self.date,
            support_level=self.support_level,
            confidence=self.confidence,
            score=score,
            summary=self.summary,
            source_ids=self.source_ids,
            relation_ids=(),
            caveats=self.caveats,
            payload={
                "metric": self.metric,
                "effect_kind": self.effect_kind,
                "baseline": self.baseline,
                "comparison": self.comparison,
                "estimate": self.estimate,
            },
        )


@dataclass(frozen=True)
class MachineAttributionClaimAnalysis:
    generated_for: dict[str, Any]
    claim_count: int
    by_support_level: dict[str, int]
    claims: list[dict[str, Any]]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_attribution_claims(
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
    experiment_claims_path: Path | None = None,
) -> MachineAttributionClaimAnalysis:
    support_payload = _optional_payload(support_assessment_path, "machine_support_assessment.json")
    experiment_payload = _optional_payload(experiment_claims_path, "machine_experiment_claims.json")
    rows = [
        *(_claim_row(row) for row in _support_rows(support_payload)),
        *(_claim_row(row) for row in _controlled_estimate_claims(experiment_payload)),
    ]
    rows = [row for row in rows if row is not None]
    by_support: dict[str, int] = {}
    for row in rows:
        by_support[str(row.support_level or "unknown")] = by_support.get(str(row.support_level or "unknown"), 0) + 1
    return MachineAttributionClaimAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": ["machine_support_assessment.json", "machine_experiment_claims.json"],
        },
        claim_count=len(rows),
        by_support_level=dict(sorted(by_support.items())),
        claims=[_claim_payload(row) for row in rows],
        caveats=[
            "claim artifact is deterministic over support assessments and experiment claim packs",
            "insufficient rows are explicit refusals; they are not failed analyses",
        ],
    )


def write_machine_attribution_claims(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    support_assessment_path: Path | None = None,
    experiment_claims_path: Path | None = None,
) -> MachineAttributionClaimAnalysis:
    analysis = analyze_machine_attribution_claims(
        start=start,
        end=end,
        support_assessment_path=support_assessment_path,
        experiment_claims_path=experiment_claims_path,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _support_rows(payload: dict[str, Any] | None) -> tuple[MachineAttributionClaim, ...]:
    if not isinstance(payload, dict):
        return ()
    rows = payload.get("assessments")
    if not isinstance(rows, list):
        return ()
    claims = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        support_level = str(row.get("support_level") or "insufficient")
        if support_level not in {"controlled", "natural_experiment", "observational", "insufficient"}:
            support_level = "insufficient"
        metric = str(row.get("metric") or "unknown_metric")
        factor = str(row.get("suspected_factor") or "unknown_factor")
        support_source_ids = tuple(
            str(value)
            for value in (
                row.get("assessment_id"),
                row.get("candidate_id"),
                *(_list(row.get("source_ids"))),
            )
            if value
        )
        claims.append(MachineAttributionClaim(
            claim_type="machine_attribution",
            project=canonical_project_name(row.get("project")) if row.get("project") else None,
            date=None,
            metric=metric,
            effect_kind=factor,
            support_level=support_level,  # type: ignore[arg-type]
            confidence=_confidence(row.get("confidence")),
            summary=str(row.get("summary") or f"{support_level} support for {factor}"),
            baseline={"candidate_id": row.get("candidate_id")},
            comparison={"decision": row.get("decision"), "support_level": support_level},
            estimate={
                "support_level": support_level,
                "refusal_reasons": row.get("refusal_reasons") if isinstance(row.get("refusal_reasons"), list) else [],
                "instrumentation_gaps": row.get("instrumentation_gaps") if isinstance(row.get("instrumentation_gaps"), list) else [],
                "source_artifacts": row.get("source_artifacts") if isinstance(row.get("source_artifacts"), list) else [],
            },
            source_ids=tuple(dict.fromkeys(support_source_ids)),
            caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
        ))
    return tuple(claims)


def _controlled_estimate_claims(payload: dict[str, Any] | None) -> tuple[MachineAttributionClaim, ...]:
    if not isinstance(payload, dict):
        return ()
    estimates = [row for row in payload.get("effect_estimates", []) if isinstance(row, dict)]
    packs = [row for row in payload.get("claim_packs", []) if isinstance(row, dict)]
    by_group: dict[str, list[dict[str, Any]]] = {}
    for pack in packs:
        group = pack.get("run_group_id")
        if group:
            by_group.setdefault(str(group), []).append(pack)
    claims = []
    for estimate in estimates:
        group = str(estimate.get("run_group_id") or "")
        if not group:
            continue
        group_packs = by_group.get(group, [])
        first = group_packs[0] if group_packs else {}
        started = parse_datetime(str(first.get("started_at") or "")) if first else None
        metric = str(estimate.get("metric") or "duration_seconds")
        control = str(estimate.get("control_label") or "control")
        treatment = str(estimate.get("treatment_label") or "treatment")
        claims.append(MachineAttributionClaim(
            claim_type="machine_attribution",
            project=_project_from_pack(first),
            date=started.date() if started else None,
            metric=metric,
            effect_kind=f"controlled_benchmark:{group}",
            support_level="controlled",
            confidence=_controlled_confidence(estimate),
            summary=_controlled_summary(
                group=group,
                treatment=treatment,
                control=control,
                metric=metric,
                estimate=estimate,
            ),
            baseline={"label": control, "n": estimate.get("control_n"), "mean": estimate.get("control_mean")},
            comparison={"label": treatment, "n": estimate.get("treatment_n"), "mean": estimate.get("treatment_mean")},
            estimate=dict(estimate),
            source_ids=tuple(str(row.get("run_id")) for row in group_packs if row.get("run_id")),
            caveats=tuple(
                str(caveat)
                for row in group_packs
                for caveat in row.get("caveats", ())
                if caveat
            ),
        ))
    return tuple(claims)


def _claim_row(claim: MachineAttributionClaim) -> AnalysisClaimRow | None:
    try:
        return claim.to_analysis_claim()
    except ValueError:
        return None


def _claim_payload(row: AnalysisClaimRow) -> dict[str, Any]:
    return {
        "claim_id": row.claim_id,
        "claim_type": row.claim_type,
        "project": row.project,
        "date": row.date.isoformat() if row.date else None,
        "support_level": row.support_level,
        "confidence": row.confidence,
        "score": row.score,
        "summary": row.summary,
        "source_ids": list(row.source_ids),
        "relation_ids": list(row.relation_ids),
        "caveats": list(row.caveats),
        "payload": row.payload,
    }


def _optional_payload(path: Path | None, name: str) -> dict[str, Any] | None:
    payload = load_json_if_exists(path or resolve_analysis_path(name))
    return payload if isinstance(payload, dict) else None


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _confidence(value: object) -> float:
    return min(1.0, max(0.0, float(value) if isinstance(value, (int, float)) else 0.0))


def _controlled_confidence(estimate: dict[str, Any]) -> float:
    total = int(estimate.get("control_n") or 0) + int(estimate.get("treatment_n") or 0)
    return min(0.9, 0.55 + total * 0.025)


def _controlled_summary(
    *,
    group: str,
    treatment: str,
    control: str,
    metric: str,
    estimate: dict[str, Any],
) -> str:
    pieces = [
        f"Controlled benchmark {group}: {treatment} minus {control}",
        f"delta {estimate.get('delta')} {metric}",
    ]
    if estimate.get("ci_low") is not None and estimate.get("ci_high") is not None:
        pieces.append(f"95% CI [{estimate.get('ci_low')}, {estimate.get('ci_high')}]")
    if estimate.get("p_value") is not None:
        pieces.append(f"p={estimate.get('p_value')}")
    if estimate.get("p_value_method"):
        pieces.append(f"method={estimate.get('p_value_method')}")
    return "; ".join(pieces)


def _project_from_pack(pack: dict[str, Any]) -> str | None:
    for key in ("git_root", "cwd", "workload"):
        value = pack.get(key)
        if value:
            project = canonical_project_name(str(value))
            if project is not None:
                return project
    return None


def _estimate_score(estimate: dict[str, Any]) -> float:
    for key in ("abs_delta", "delta", "effect_size"):
        value = estimate.get(key)
        if isinstance(value, (int, float)):
            return abs(float(value))
    return 0.0


def _validate_confidence(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError("MachineAttributionClaim.confidence must be between 0 and 1")


__all__ = [
    "MachineAttributionClaim",
    "MachineAttributionClaimAnalysis",
    "SupportLevel",
    "analyze_machine_attribution_claims",
    "write_machine_attribution_claims",
]
