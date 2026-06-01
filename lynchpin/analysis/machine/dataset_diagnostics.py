"""Diagnostics for mining the extant machine/work-observation dataset."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineFeatureDatasetAudit:
    frame_id: str
    unit_type: str
    outcome_metric: str
    row_count: int
    observed_count: int
    censored_count: int
    censoring_rate: float
    invalid_leakage_count: int
    missing_value_count: int
    top_missingness: tuple[dict[str, Any], ...]
    temporal_span_days: int | None
    temporal_fold_count: int
    fold_policy: str
    status: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineMiningSearchAudit:
    scan_count: int
    comparison_universe_size: int
    emitted_candidate_count: int
    candidate_ratio: float | None
    multiplicity_status: str
    dimensions: tuple[str, ...]
    policies: tuple[str, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineDatasetDiagnostic:
    diagnostic_id: str
    diagnostic_kind: str
    status: str
    severity: str
    evidence: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class MachineDatasetDiagnosticsAnalysis:
    generated_for: dict[str, Any]
    feature_audit: MachineFeatureDatasetAudit
    mining_audit: MachineMiningSearchAudit
    diagnostic_count: int
    diagnostics: list[MachineDatasetDiagnostic]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_dataset_diagnostics(
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    mining_path: Path | None = None,
    min_fold_rows: int = 5,
) -> MachineDatasetDiagnosticsAnalysis:
    features_payload = load_json_object(
        feature_frames_path or resolve_analysis_path("machine_analysis_feature_frames.json"),
        label="machine analysis feature frames",
    )
    mining_payload = load_json_object(
        mining_path or resolve_analysis_path("machine_mining.json"),
        label="machine mining",
    )
    frame = features_payload.get("frame") if isinstance(features_payload, dict) else {}
    rows = frame.get("rows") if isinstance(frame, dict) else []
    if not isinstance(rows, list):
        rows = []

    feature_audit = _feature_audit(frame if isinstance(frame, dict) else {}, rows, min_fold_rows=min_fold_rows)
    mining_audit = _mining_audit(mining_payload if isinstance(mining_payload, dict) else {})
    diagnostics = _diagnostics(feature_audit, mining_audit)
    return MachineDatasetDiagnosticsAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "feature_source": "machine_analysis_feature_frames.json",
            "mining_source": "machine_mining.json",
            "min_fold_rows": min_fold_rows,
        },
        feature_audit=feature_audit,
        mining_audit=mining_audit,
        diagnostic_count=len(diagnostics),
        diagnostics=diagnostics,
        caveats=[
            "diagnostics describe the extant observational dataset; they do not execute new tests",
            "search-space and fold checks are gates against over-interpreting mined candidates",
        ],
    )


def write_machine_dataset_diagnostics(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    mining_path: Path | None = None,
    min_fold_rows: int = 5,
) -> MachineDatasetDiagnosticsAnalysis:
    analysis = analyze_machine_dataset_diagnostics(
        start=start,
        end=end,
        feature_frames_path=feature_frames_path,
        mining_path=mining_path,
        min_fold_rows=min_fold_rows,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _feature_audit(frame: dict[str, Any], rows: list[Any], *, min_fold_rows: int) -> MachineFeatureDatasetAudit:
    dict_rows = [row for row in rows if isinstance(row, dict)]
    observed = [row for row in dict_rows if row.get("censoring_status") == "observed"]
    censored = len(dict_rows) - len(observed)
    leakage_invalid = sum(1 for row in dict_rows if row.get("leakage_status") != "ok")
    missing_counter: Counter[str] = Counter()
    for row in dict_rows:
        missingness = row.get("missingness") if isinstance(row.get("missingness"), dict) else {}
        missing_counter.update(key for key, value in missingness.items() if value)
    instants = sorted(
        instant
        for row in dict_rows
        if (instant := _parse_instant(row.get("outcome_window_start"))) is not None
    )
    fold_count = _temporal_fold_count(instants, min_fold_rows=min_fold_rows)
    caveats: list[str] = []
    if censored:
        caveats.append("censored or failed rows are present and must be modeled or excluded explicitly")
    if leakage_invalid:
        caveats.append("at least one row has invalid leakage status")
    if fold_count < 2:
        caveats.append("too few temporal folds for honest discovery/validation splitting")
    status = "ready_for_mining" if dict_rows and leakage_invalid == 0 and fold_count >= 2 else "limited"
    if not dict_rows:
        status = "missing"
    span = (instants[-1].date() - instants[0].date()).days + 1 if instants else None
    return MachineFeatureDatasetAudit(
        frame_id=str(frame.get("frame_id") or ""),
        unit_type=str(frame.get("unit_type") or "unknown"),
        outcome_metric=str(frame.get("outcome_metric") or "unknown"),
        row_count=len(dict_rows),
        observed_count=len(observed),
        censored_count=censored,
        censoring_rate=round(censored / len(dict_rows), 6) if dict_rows else 0.0,
        invalid_leakage_count=leakage_invalid,
        missing_value_count=sum(missing_counter.values()),
        top_missingness=tuple(
            {"field": field, "missing_count": int(count)}
            for field, count in missing_counter.most_common(10)
        ),
        temporal_span_days=span,
        temporal_fold_count=fold_count,
        fold_policy=f"calendar-day folds with at least {min_fold_rows} rows",
        status=status,
        caveats=tuple(caveats),
    )


def _mining_audit(payload: dict[str, Any]) -> MachineMiningSearchAudit:
    scan = payload.get("scan") if isinstance(payload.get("scan"), dict) else {}
    scans = payload.get("scans") if isinstance(payload.get("scans"), list) else []
    universe = _int(scan.get("comparison_universe_size"))
    emitted = _int(scan.get("emitted_candidate_count") or payload.get("cohort_count"))
    ratio = round(emitted / universe, 6) if universe else None
    policies = [
        str(row.get("multiplicity_policy"))
        for row in ([scan, *[item for item in scans if isinstance(item, dict)]])
        if row.get("multiplicity_policy")
    ]
    status = "registered" if universe and emitted <= universe and policies else "limited"
    caveats = []
    if universe == 0:
        caveats.append("no registered comparison universe")
    if not policies:
        caveats.append("missing multiplicity/search-space policy")
    if ratio is not None and ratio > 0.5:
        caveats.append("more than half the search universe was emitted as candidates")
    return MachineMiningSearchAudit(
        scan_count=int(payload.get("scan_count") or len(scans) or (1 if scan else 0)),
        comparison_universe_size=universe,
        emitted_candidate_count=emitted,
        candidate_ratio=ratio,
        multiplicity_status=status,
        dimensions=tuple(str(item) for item in scan.get("dimensions", ()) if item),
        policies=tuple(dict.fromkeys(policies)),
        caveats=tuple(caveats),
    )


def _diagnostics(
    feature_audit: MachineFeatureDatasetAudit,
    mining_audit: MachineMiningSearchAudit,
) -> list[MachineDatasetDiagnostic]:
    rows = [
        MachineDatasetDiagnostic(
            diagnostic_id="machine-dataset:feature-frame-coverage",
            diagnostic_kind="feature_frame_coverage",
            status=feature_audit.status,
            severity="blocking" if feature_audit.status == "missing" else ("warning" if feature_audit.status == "limited" else "info"),
            evidence=(
                f"rows={feature_audit.row_count}",
                f"observed={feature_audit.observed_count}",
                f"censored={feature_audit.censored_count}",
                f"temporal_folds={feature_audit.temporal_fold_count}",
            ),
            next_action="promote more work observations or narrow claims to descriptive summaries"
            if feature_audit.status != "ready_for_mining"
            else "eligible for exploratory mining with validation gates",
        ),
        MachineDatasetDiagnostic(
            diagnostic_id="machine-dataset:search-space-registration",
            diagnostic_kind="search_space_registration",
            status=mining_audit.multiplicity_status,
            severity="warning" if mining_audit.multiplicity_status != "registered" else "info",
            evidence=(
                f"scan_count={mining_audit.scan_count}",
                f"comparison_universe={mining_audit.comparison_universe_size}",
                f"emitted_candidates={mining_audit.emitted_candidate_count}",
                f"candidate_ratio={mining_audit.candidate_ratio}",
            ),
            next_action="record comparison universe and multiplicity policy before ranking candidates"
            if mining_audit.multiplicity_status != "registered"
            else "candidate ranking can cite the registered search denominator",
        ),
    ]
    if feature_audit.missing_value_count:
        rows.append(MachineDatasetDiagnostic(
            diagnostic_id="machine-dataset:missingness",
            diagnostic_kind="missingness",
            status="present",
            severity="warning",
            evidence=tuple(
                f"{row['field']}={row['missing_count']}" for row in feature_audit.top_missingness
            ) or (f"missing_values={feature_audit.missing_value_count}",),
            next_action="prefer analyses robust to missing covariates; do not impute silently",
        ))
    return rows


def _temporal_fold_count(instants: list[datetime], *, min_fold_rows: int) -> int:
    by_day: Counter[date] = Counter(instant.date() for instant in instants)
    return sum(1 for count in by_day.values() if count >= min_fold_rows)


def _parse_instant(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "MachineDatasetDiagnostic",
    "MachineDatasetDiagnosticsAnalysis",
    "MachineFeatureDatasetAudit",
    "MachineMiningSearchAudit",
    "analyze_machine_dataset_diagnostics",
    "write_machine_dataset_diagnostics",
]
