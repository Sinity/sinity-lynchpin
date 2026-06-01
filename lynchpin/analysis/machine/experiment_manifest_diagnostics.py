"""Diagnostics for executed machine-experiment manifest ingestion."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest
from lynchpin.core.io import save_json
from lynchpin.sources.machine_experiments import experiment_root, experiment_runs


@dataclass(frozen=True)
class MachineExperimentManifestDiagnostic:
    path: str
    relative_path: str
    manifest_kind: str
    schema: str | None
    run_id: str | None
    run_group_id: str | None
    started_at: str | None
    ended_at: str | None
    in_window: bool | None
    source_loadable: bool
    controlled_benchmark_valid: bool
    internal_json_path: str | None
    issues: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class MachineExperimentManifestDiagnostics:
    generated_for: dict[str, Any]
    root: str
    root_exists: bool
    manifest_count: int
    source_loadable_count: int
    controlled_benchmark_valid_count: int
    validation_issue_count: int
    promotion_issue_count: int
    controlled_run_invalid_count: int
    legacy_observational_count: int
    template_count: int
    out_of_window_count: int
    by_kind: dict[str, int]
    diagnostics: list[MachineExperimentManifestDiagnostic]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_experiment_manifest_diagnostics(
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
    require_file_refs: bool = False,
) -> MachineExperimentManifestDiagnostics:
    base = experiment_root(root)
    accepted_paths = {
        str(run.manifest_path)
        for run in experiment_runs(start=start, end=end, root=base)
    }
    diagnostics = [
        _diagnose_manifest(
            manifest,
            root=base,
            accepted_paths=accepted_paths,
            start=start,
            end=end,
            require_file_refs=require_file_refs,
        )
        for manifest in sorted(base.rglob("manifest.json")) if base.exists()
    ]
    by_kind: dict[str, int] = {}
    for row in diagnostics:
        by_kind[row.manifest_kind] = by_kind.get(row.manifest_kind, 0) + 1
    return MachineExperimentManifestDiagnostics(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "require_file_refs": require_file_refs,
        },
        root=str(base),
        root_exists=base.exists(),
        manifest_count=len(diagnostics),
        source_loadable_count=sum(1 for row in diagnostics if row.source_loadable),
        controlled_benchmark_valid_count=sum(
            1 for row in diagnostics if row.controlled_benchmark_valid
        ),
        validation_issue_count=sum(1 for row in diagnostics if row.issues),
        promotion_issue_count=sum(1 for row in diagnostics if _promotion_blocked(row)),
        controlled_run_invalid_count=sum(
            1 for row in diagnostics
            if row.manifest_kind == "executed_run" and not row.controlled_benchmark_valid
        ),
        legacy_observational_count=sum(
            1 for row in diagnostics
            if row.manifest_kind == "legacy_or_ad_hoc_run" and row.source_loadable
        ),
        template_count=sum(1 for row in diagnostics if row.manifest_kind == "template"),
        out_of_window_count=sum(1 for row in diagnostics if row.in_window is False),
        by_kind=dict(sorted(by_kind.items())),
        diagnostics=diagnostics,
        caveats=_caveats(base=base, diagnostics=diagnostics),
    )


def write_machine_experiment_manifest_diagnostics(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
    require_file_refs: bool = False,
) -> MachineExperimentManifestDiagnostics:
    analysis = analyze_machine_experiment_manifest_diagnostics(
        start=start,
        end=end,
        root=root,
        require_file_refs=require_file_refs,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _diagnose_manifest(
    path: Path,
    *,
    root: Path,
    accepted_paths: set[str],
    start: date | None,
    end: date | None,
    require_file_refs: bool,
) -> MachineExperimentManifestDiagnostic:
    issues: list[str] = []
    warnings: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _row(
            path,
            root=root,
            manifest_kind="unreadable_json",
            issues=(f"cannot read manifest JSON: {exc}",),
            warnings=(),
        )
    if not isinstance(payload, dict):
        return _row(
            path,
            root=root,
            manifest_kind="non_object",
            issues=("manifest root must be an object",),
            warnings=(),
        )

    schema = _str(payload.get("schema"))
    manifest_kind = _manifest_kind(payload)
    validation = validate_executed_benchmark_manifest(
        payload,
        manifest_path=path,
        require_file_refs=require_file_refs,
    )
    issues.extend(validation.issues)
    warnings.extend(validation.warnings)
    started_day = _date_from_iso(validation.started_at)
    in_window = _in_window(started_day, start=start, end=end)
    source_loadable = str(path) in accepted_paths
    if in_window is False:
        warnings.append("manifest is outside the selected analysis window")
    if source_loadable and not validation.valid:
        warnings.append("manifest is source-loadable as observational evidence but not controlled-valid")
    if validation.valid and not source_loadable and in_window is not False:
        issues.append("manifest validates but is not loadable by machine_experiments source")
    return MachineExperimentManifestDiagnostic(
        path=str(path),
        relative_path=_relative(path, root),
        manifest_kind=manifest_kind,
        schema=schema,
        run_id=validation.run_id,
        run_group_id=validation.run_group_id,
        started_at=validation.started_at,
        ended_at=validation.ended_at,
        in_window=in_window,
        source_loadable=source_loadable,
        controlled_benchmark_valid=validation.valid,
        internal_json_path=validation.internal_json_path,
        issues=tuple(dict.fromkeys(issues)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _row(
    path: Path,
    *,
    root: Path,
    manifest_kind: str,
    issues: tuple[str, ...],
    warnings: tuple[str, ...],
) -> MachineExperimentManifestDiagnostic:
    return MachineExperimentManifestDiagnostic(
        path=str(path),
        relative_path=_relative(path, root),
        manifest_kind=manifest_kind,
        schema=None,
        run_id=None,
        run_group_id=None,
        started_at=None,
        ended_at=None,
        in_window=None,
        source_loadable=False,
        controlled_benchmark_valid=False,
        internal_json_path=None,
        issues=issues,
        warnings=warnings,
    )


def _manifest_kind(payload: dict[str, Any]) -> str:
    schema = payload.get("schema")
    if schema == "lynchpin.machine_experiment.template.v1" or payload.get("template_status") is not None:
        return "template"
    if schema == "lynchpin.machine_experiment.run.v1":
        return "executed_run"
    return "legacy_or_ad_hoc_run"


def _str(value: object) -> str | None:
    return str(value) if value is not None else None


def _date_from_iso(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _in_window(value: date | None, *, start: date | None, end: date | None) -> bool | None:
    if value is None:
        return None
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _promotion_blocked(row: MachineExperimentManifestDiagnostic) -> bool:
    if row.manifest_kind == "template":
        return False
    return row.in_window is not False and not row.source_loadable


def _caveats(*, base: Path, diagnostics: list[MachineExperimentManifestDiagnostic]) -> list[str]:
    caveats: list[str] = []
    if not base.exists():
        caveats.append("experiment manifest root does not exist")
    if any(row.manifest_kind == "template" for row in diagnostics):
        caveats.append("template manifests are export handoff artifacts, not executed evidence")
    if any(row.source_loadable and not row.controlled_benchmark_valid for row in diagnostics):
        caveats.append("source-loadable smoke/ad hoc manifests remain observational until controlled-valid")
    if any(row.manifest_kind == "executed_run" and row.issues for row in diagnostics):
        caveats.append("one or more executed-run manifests have validation issues")
    return caveats


__all__ = [
    "MachineExperimentManifestDiagnostic",
    "MachineExperimentManifestDiagnostics",
    "analyze_machine_experiment_manifest_diagnostics",
    "write_machine_experiment_manifest_diagnostics",
]
