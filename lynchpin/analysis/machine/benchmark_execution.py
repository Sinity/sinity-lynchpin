"""Operational runner for selected machine benchmark handoff groups."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from typing import Any

from lynchpin.analysis.machine.benchmark_execution_handoff import (
    analyze_machine_benchmark_execution_handoff,
)
from lynchpin.analysis.machine.benchmark_manifest_bundle import (
    MachineBenchmarkManifestBundle,
    export_machine_benchmark_manifest_bundle,
    analyze_machine_benchmark_manifest_bundle,
)
from lynchpin.analysis.machine.controlled_benchmarks import (
    validate_executed_benchmark_manifest,
)
from lynchpin.core.io import save_json
from lynchpin.sources.machine_experiments import experiment_root


@dataclass(frozen=True)
class BenchmarkRunScriptResult:
    run_id: str
    run_group_id: str
    script_path: str
    manifest_path: str
    executed: bool
    exit_code: int | None
    validation_valid: bool | None
    validation_issues: tuple[str, ...]
    validation_warnings: tuple[str, ...]


@dataclass(frozen=True)
class SelectedBenchmarkExecution:
    generated_at_utc: str
    run_group_id: str
    candidate_id: str
    ready_to_export: bool
    execute: bool
    materialize_after: bool
    output_dir: str
    written_paths: tuple[str, ...]
    run_scripts: tuple[BenchmarkRunScriptResult, ...]
    materialization_commands: tuple[tuple[str, ...], ...]
    materialization_exit_codes: tuple[int, ...]
    next_actions: tuple[str, ...]
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_selected_benchmark_group(
    *,
    run_group_id: str | None = None,
    candidate_id: str | None = None,
    output_dir: Path | None = None,
    candidates_path: Path | None = None,
    manifest_bundle_path: Path | None = None,
    preflight_path: Path | None = None,
    support_path: Path | None = None,
    execute: bool = False,
    materialize_after: bool = False,
    overwrite: bool = False,
    queue_limit: int = 0,
    start: date | None = None,
    end: date | None = None,
    require_ready: bool = True,
) -> SelectedBenchmarkExecution:
    """Export, optionally execute, then optionally rescore one benchmark group.

    The command deliberately reuses the existing generated ``run.sh`` scripts
    and coherent post-execution materialization commands. It does not create a
    parallel benchmark ingestion path.
    """
    handoff = analyze_machine_benchmark_execution_handoff(
        candidates_path=candidates_path,
        manifest_bundle_path=manifest_bundle_path,
        preflight_path=preflight_path,
        support_path=support_path,
        limit=queue_limit,
    )
    item = _select_handoff_item(
        [asdict(row) for row in handoff.items],
        run_group_id=run_group_id,
        candidate_id=candidate_id,
        require_ready=require_ready,
    )
    bundle = analyze_machine_benchmark_manifest_bundle(
        plans_path=None,
        limit=0,
    )
    if manifest_bundle_path is not None:
        bundle = _bundle_from_payload(manifest_bundle_path)
    group = next(
        (row for row in bundle.groups if row.run_group_id == item["run_group_id"]),
        None,
    )
    if group is None:
        raise ValueError(f"benchmark group {item['run_group_id']!r} is absent from manifest bundle")

    target_dir = output_dir or experiment_root()
    selected_bundle = MachineBenchmarkManifestBundle(
        generated_for={
            **bundle.generated_for,
            "selected_run_group_id": group.run_group_id,
            "selected_candidate_id": group.candidate_id,
        },
        group_count=1,
        run_template_count=group.run_count,
        groups=[group],
        caveats=bundle.caveats,
    )
    written = export_machine_benchmark_manifest_bundle(
        selected_bundle,
        target_dir,
        overwrite=overwrite,
        write_runner=True,
    )
    scripts = tuple(sorted(target_dir.glob(f"{group.run_group_id}/runs/*/run.sh")))
    script_results = tuple(
        _run_or_validate_script(script, execute=execute)
        for script in scripts
    )
    materialization_commands = _materialization_commands(
        start=start,
        end=end,
        script_results=script_results,
    )
    materialization_exit_codes: tuple[int, ...] = ()
    if materialize_after:
        materialization_exit_codes = tuple(subprocess.run(command, check=False).returncode for command in materialization_commands)

    next_actions = _next_actions(
        execute=execute,
        materialize_after=materialize_after,
        script_results=script_results,
        materialization_commands=materialization_commands,
        materialization_exit_codes=materialization_exit_codes,
    )
    return SelectedBenchmarkExecution(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        run_group_id=group.run_group_id,
        candidate_id=group.candidate_id,
        ready_to_export=bool(item.get("ready_to_export")),
        execute=execute,
        materialize_after=materialize_after,
        output_dir=str(target_dir),
        written_paths=tuple(str(path) for path in written),
        run_scripts=script_results,
        materialization_commands=materialization_commands,
        materialization_exit_codes=materialization_exit_codes,
        next_actions=next_actions,
        caveats=tuple(sorted(dict.fromkeys([
            *bundle.caveats,
            "execution uses exported per-run scripts; failed workload exit codes are still manifest evidence when manifest.json is written",
            "post-execution materialization uses the coherent materialize/promote path so experiment rows join the same substrate snapshot as telemetry",
        ]))),
    )


def write_selected_benchmark_execution(
    out: Path,
    **kwargs: Any,
) -> SelectedBenchmarkExecution:
    result = run_selected_benchmark_group(**kwargs)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_json(out, json.loads(json.dumps(result.to_dict(), default=str)), sort_keys=True)
    return result


def _select_handoff_item(
    items: list[dict[str, Any]],
    *,
    run_group_id: str | None,
    candidate_id: str | None,
    require_ready: bool,
) -> dict[str, Any]:
    rows = [
        row for row in items
        if (run_group_id is None or row.get("run_group_id") == run_group_id)
        and (candidate_id is None or row.get("candidate_id") == candidate_id)
        and (not require_ready or row.get("ready_to_export") is True)
    ]
    if not rows:
        target = run_group_id or candidate_id or "top ready group"
        raise ValueError(f"no benchmark execution handoff item matches {target!r}")
    rows.sort(key=lambda row: (not bool(row.get("pareto_frontier")), -float(row.get("priority_score") or 0), str(row.get("run_group_id") or "")))
    return rows[0]


def _bundle_from_payload(path: Path) -> MachineBenchmarkManifestBundle:
    from lynchpin.analysis.machine.benchmark_manifest_bundle import (
        MachineBenchmarkManifestGroup,
        MachineBenchmarkRunTemplate,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark manifest bundle must be an object: {path}")
    groups = []
    for group in payload.get("groups", []):
        if not isinstance(group, dict):
            continue
        templates = tuple(
            MachineBenchmarkRunTemplate(
                run_id=str(row.get("run_id") or ""),
                run_group_id=str(row.get("run_group_id") or group.get("run_group_id") or ""),
                sequence_index=int(row.get("sequence_index") or 0),
                treatment_label=str(row.get("treatment_label") or ""),
                cache_condition=str(row.get("cache_condition") or ""),
                derivation_key=str(row["derivation_key"]) if row.get("derivation_key") is not None else None,
                telemetry_window_id=str(row.get("telemetry_window_id") or ""),
                manifest=row.get("manifest") if isinstance(row.get("manifest"), dict) else {},
            )
            for row in group.get("run_templates", [])
            if isinstance(row, dict)
        )
        groups.append(
            MachineBenchmarkManifestGroup(
                run_group_id=str(group.get("run_group_id") or ""),
                plan_id=str(group.get("plan_id") or ""),
                candidate_id=str(group.get("candidate_id") or ""),
                planning_status=str(group.get("planning_status") or "ready"),
                support_ceiling=str(group.get("support_ceiling") or "controlled"),
                primary_metric=str(group.get("primary_metric") or ""),
                run_count=int(group.get("run_count") or len(templates)),
                run_templates=templates,
                pre_analysis=group.get("pre_analysis") if isinstance(group.get("pre_analysis"), dict) else {},
                caveats=tuple(str(item) for item in group.get("caveats", ()) if item),
            )
        )
    return MachineBenchmarkManifestBundle(
        generated_for=payload.get("generated_for") if isinstance(payload.get("generated_for"), dict) else {},
        group_count=int(payload.get("group_count") or len(groups)),
        run_template_count=int(payload.get("run_template_count") or sum(group.run_count for group in groups)),
        groups=groups,
        caveats=[str(item) for item in payload.get("caveats", ()) if item],
    )


def _run_or_validate_script(script: Path, *, execute: bool) -> BenchmarkRunScriptResult:
    if execute:
        completed = subprocess.run(["bash", str(script)], cwd=str(script.parent), check=False)
        exit_code: int | None = completed.returncode
    else:
        exit_code = None
    manifest = script.parent / "manifest.json"
    validation_valid: bool | None = None
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    if manifest.exists():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            issues = ("manifest root must be an object",)
            validation_valid = False
        else:
            validation = validate_executed_benchmark_manifest(
                payload,
                manifest_path=manifest,
                require_file_refs=True,
            )
            validation_valid = validation.valid
            issues = validation.issues
            warnings = validation.warnings
    run_group_id = script.parents[2].name
    return BenchmarkRunScriptResult(
        run_id=script.parent.name,
        run_group_id=run_group_id,
        script_path=str(script),
        manifest_path=str(manifest),
        executed=execute,
        exit_code=exit_code,
        validation_valid=validation_valid,
        validation_issues=issues,
        validation_warnings=warnings,
    )


def _materialization_commands(
    *,
    start: date | None,
    end: date | None,
    script_results: tuple[BenchmarkRunScriptResult, ...],
) -> tuple[tuple[str, ...], ...]:
    start, end = _materialization_window(start=start, end=end, script_results=script_results)
    return (
        (
            "python",
            "-m",
            "lynchpin.cli.materialize",
            "--all",
            "--promote",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--progress",
            "quiet",
        ),
        (
            "python",
            "-m",
            "lynchpin.analysis",
            "materialize-machine",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--up-to",
            "machine_analysis_readiness",
        ),
    )


def _materialization_window(
    *,
    start: date | None,
    end: date | None,
    script_results: tuple[BenchmarkRunScriptResult, ...],
) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    manifest_days = []
    for row in script_results:
        manifest = Path(row.manifest_path)
        if not manifest.exists():
            continue
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("started_at"), str):
            continue
        try:
            manifest_days.append(datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00")).date())
        except ValueError:
            continue
    today = datetime.now().astimezone().date()
    selected_start = start or (min(manifest_days) if manifest_days else today)
    selected_end = end or ((max(manifest_days) if manifest_days else today) + timedelta(days=1))
    return selected_start, selected_end


def _next_actions(
    *,
    execute: bool,
    materialize_after: bool,
    script_results: tuple[BenchmarkRunScriptResult, ...],
    materialization_commands: tuple[tuple[str, ...], ...],
    materialization_exit_codes: tuple[int, ...],
) -> tuple[str, ...]:
    actions: list[str] = []
    if not execute:
        actions.extend(f"bash {row.script_path}" for row in script_results)
    elif any(row.validation_valid is not True for row in script_results):
        actions.append("inspect invalid or missing manifest.json files before promoting")
    if not materialize_after:
        actions.extend(" ".join(command) for command in materialization_commands)
    elif any(code != 0 for code in materialization_exit_codes):
        actions.append("materialization command failed; inspect command output before treating benchmark claims as rescored")
    else:
        actions.append("inspect machine_benchmark_estimates and machine_attribution_claims for the selected run group")
    return tuple(actions)


__all__ = [
    "BenchmarkRunScriptResult",
    "SelectedBenchmarkExecution",
    "run_selected_benchmark_group",
    "write_selected_benchmark_execution",
]
