"""Exportable controlled-benchmark manifest templates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from copy import deepcopy
import json
from pathlib import Path
import shlex
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineBenchmarkRunTemplate:
    run_id: str
    run_group_id: str
    sequence_index: int
    treatment_label: str
    cache_condition: str
    derivation_key: str | None
    telemetry_window_id: str
    manifest: dict[str, Any]


@dataclass(frozen=True)
class MachineBenchmarkManifestGroup:
    run_group_id: str
    plan_id: str
    candidate_id: str
    planning_status: str
    support_ceiling: str
    primary_metric: str
    run_count: int
    run_templates: tuple[MachineBenchmarkRunTemplate, ...]
    pre_analysis: dict[str, Any]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineBenchmarkManifestBundle:
    generated_for: dict[str, Any]
    group_count: int
    run_template_count: int
    groups: list[MachineBenchmarkManifestGroup]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_benchmark_manifest_bundle(
    *,
    start: date | None = None,
    end: date | None = None,
    plans_path: Path | None = None,
    limit: int = 10,
) -> MachineBenchmarkManifestBundle:
    payload = load_json_object(
        plans_path or resolve_analysis_path("machine_benchmark_plans.json"),
        label="machine benchmark plans",
    )
    plans = [row for row in payload.get("plans", []) if isinstance(row, dict)]
    groups = []
    for plan in plans:
        if plan.get("planning_status") != "ready":
            continue
        group = _group(plan)
        if group is not None:
            groups.append(group)
    if limit > 0:
        groups = groups[:limit]
    return MachineBenchmarkManifestBundle(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_benchmark_plans.json",
            "limit": limit,
        },
        group_count=len(groups),
        run_template_count=sum(group.run_count for group in groups),
        groups=groups,
        caveats=[
            "templates are not executed benchmark manifests",
            "runner must fill started_at/ended_at/exit_status/pre_state/post_state/git before promotion",
            "files are written as manifest.template.json so source ingestion cannot mistake them for completed runs",
        ],
    )


def write_machine_benchmark_manifest_bundle(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    plans_path: Path | None = None,
    limit: int = 10,
) -> MachineBenchmarkManifestBundle:
    analysis = analyze_machine_benchmark_manifest_bundle(
        start=start,
        end=end,
        plans_path=plans_path,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def export_machine_benchmark_manifest_bundle(
    bundle: MachineBenchmarkManifestBundle,
    output_dir: Path,
    *,
    overwrite: bool = False,
    write_runner: bool = True,
) -> tuple[Path, ...]:
    written: list[Path] = []
    for group in bundle.groups:
        group_dir = output_dir / group.run_group_id
        group_path = group_dir / "plan.json"
        _write_json(group_path, _group_payload(group), overwrite=overwrite)
        written.append(group_path)
        for run in group.run_templates:
            run_path = group_dir / "runs" / run.run_id / "manifest.template.json"
            manifest = _export_manifest(run.manifest, run_dir=run_path.parent)
            _write_json(run_path, manifest, overwrite=overwrite)
            written.append(run_path)
            if write_runner:
                runner_path = run_path.parent / "run.sh"
                _write_text(runner_path, _runner_script(manifest), overwrite=overwrite, executable=True)
                written.append(runner_path)
    return tuple(written)


def _export_manifest(manifest: dict[str, Any], *, run_dir: Path) -> dict[str, Any]:
    """Bind template capture paths to the concrete exported run directory."""
    payload = deepcopy(manifest)
    internal_json_path = str(run_dir / "nix-internal-json.ndjson")
    payload["nix_internal_json_path"] = internal_json_path
    planned = payload.get("planned_treatment") if isinstance(payload.get("planned_treatment"), dict) else {}
    selected = planned.get("selected_run") if isinstance(planned.get("selected_run"), dict) else {}
    selected["internal_json_path"] = internal_json_path
    planned["selected_run"] = selected
    benchmark = planned.get("controlled_benchmark") if isinstance(planned.get("controlled_benchmark"), dict) else {}
    internal_json = benchmark.get("internal_json") if isinstance(benchmark.get("internal_json"), dict) else {}
    internal_json["path"] = internal_json_path
    internal_json.setdefault("log_format", "internal-json")
    internal_json.setdefault("capture_stream", "stderr")
    internal_json.setdefault(
        "argv_template",
        ["nix", "build", "--log-format", "internal-json", "{derivation_key}"],
    )
    benchmark["internal_json"] = internal_json
    planned["controlled_benchmark"] = benchmark
    payload["planned_treatment"] = planned
    notes = list(payload.get("notes") if isinstance(payload.get("notes"), list) else [])
    notes.append("nix_internal_json_path is materialized for this exported run directory")
    payload["notes"] = notes
    return payload


def _group(plan: dict[str, Any]) -> MachineBenchmarkManifestGroup | None:
    preview = plan.get("manifest_preview") if isinstance(plan.get("manifest_preview"), dict) else {}
    controlled = preview.get("controlled_benchmark") if isinstance(preview.get("controlled_benchmark"), dict) else {}
    pre_analysis = preview.get("pre_analysis") if isinstance(preview.get("pre_analysis"), dict) else {}
    run_manifest = [row for row in plan.get("run_manifest", []) if isinstance(row, dict)]
    if not run_manifest:
        return None
    run_group_id = str(controlled.get("run_group_id") or run_manifest[0].get("run_group_id") or plan.get("plan_id"))
    templates = tuple(_run_template(plan, preview, row, run_group_id=run_group_id) for row in run_manifest)
    return MachineBenchmarkManifestGroup(
        run_group_id=run_group_id,
        plan_id=str(plan.get("plan_id") or ""),
        candidate_id=str(plan.get("candidate_id") or ""),
        planning_status=str(plan.get("planning_status") or ""),
        support_ceiling=str(plan.get("support_ceiling") or ""),
        primary_metric=str(plan.get("primary_metric") or controlled.get("metric") or ""),
        run_count=len(templates),
        run_templates=templates,
        pre_analysis=pre_analysis,
        caveats=tuple(str(item) for item in plan.get("caveats", ()) if item),
    )


def _run_template(
    plan: dict[str, Any],
    preview: dict[str, Any],
    row: dict[str, Any],
    *,
    run_group_id: str,
) -> MachineBenchmarkRunTemplate:
    run_id = str(row.get("run_id") or "")
    treatment_label = str(row.get("treatment_label") or "")
    cache_condition = str(row.get("cache_condition") or "")
    planned_treatment = {
        **preview,
        "selected_run": {
            "run_id": run_id,
            "sequence_index": row.get("sequence_index"),
            "treatment_label": treatment_label,
            "cache_condition": cache_condition,
            "derivation_key": row.get("derivation_key"),
            "telemetry_window_id": row.get("telemetry_window_id"),
            "internal_json_path": row.get("internal_json_path"),
        },
    }
    manifest = {
        "schema": "lynchpin.machine_experiment.template.v1",
        "template_status": "planned_not_executed",
        "run_id": run_id,
        "run_group_id": run_group_id,
        "host": "<fill-host-at-execution>",
        "workload": str(plan.get("manifest_preview", {}).get("workload") or plan.get("primary_metric") or ""),
        "command": [],
        "cwd": None,
        "started_at": None,
        "ended_at": None,
        "monotonic_started_ns": None,
        "monotonic_ended_ns": None,
        "exit_status": None,
        "execution_outcome": {
            "status": None,
            "timeout_s": None,
            "censored": None,
            "retry_attempt": 1,
            "warmup_discarded": False,
            "partial_output": None,
        },
        "service_profile": None,
        "cache_profile": cache_condition,
        "measurement_context": {
            "host_boot_id": None,
            "system_generation": None,
            "kernel_release": None,
            "cpu_governor": None,
            "power_profile": None,
            "thermal_zone_policy": None,
            "cache_conditioning_policy": None,
            "env_digest": {},
        },
        "planned_treatment": planned_treatment,
        "git": {"root": None, "head": None, "branch": None, "dirty": None},
        "pre_state": {},
        "post_state": {},
        "notes": [
            "template only; runner must rename/write manifest.json after execution",
            f"plan_id={plan.get('plan_id')}",
        ],
    }
    return MachineBenchmarkRunTemplate(
        run_id=run_id,
        run_group_id=run_group_id,
        sequence_index=int(row.get("sequence_index") or 0),
        treatment_label=treatment_label,
        cache_condition=cache_condition,
        derivation_key=str(row["derivation_key"]) if row.get("derivation_key") is not None else None,
        telemetry_window_id=str(row.get("telemetry_window_id") or ""),
        manifest=manifest,
    )


def _group_payload(group: MachineBenchmarkManifestGroup) -> dict[str, Any]:
    return {
        "schema": "lynchpin.machine_benchmark.plan.v1",
        "run_group_id": group.run_group_id,
        "plan_id": group.plan_id,
        "candidate_id": group.candidate_id,
        "planning_status": group.planning_status,
        "support_ceiling": group.support_ceiling,
        "primary_metric": group.primary_metric,
        "run_count": group.run_count,
        "pre_analysis": group.pre_analysis,
        "run_ids": [row.run_id for row in group.run_templates],
        "caveats": list(group.caveats),
    }


def _runner_script(manifest: dict[str, Any]) -> str:
    """Return a concrete future-run script without executing it now."""
    planned = manifest.get("planned_treatment") if isinstance(manifest.get("planned_treatment"), dict) else {}
    selected = planned.get("selected_run") if isinstance(planned.get("selected_run"), dict) else {}
    benchmark = planned.get("controlled_benchmark") if isinstance(planned.get("controlled_benchmark"), dict) else {}
    internal_json = benchmark.get("internal_json") if isinstance(benchmark.get("internal_json"), dict) else {}
    argv = internal_json.get("argv_template") if isinstance(internal_json.get("argv_template"), list) else []
    derivation_key = str(selected.get("derivation_key") or "")
    cache_condition = str(selected.get("cache_condition") or "")
    command = [
        str(part).replace("{derivation_key}", derivation_key)
        for part in argv
        if str(part)
    ] or ["nix", "build", "--log-format", "internal-json", derivation_key]
    command_literal = " ".join(shlex.quote(part) for part in command)
    command_json = json.dumps(command)
    return f"""#!/usr/bin/env bash
set -euo pipefail

run_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
template="$run_dir/manifest.template.json"
manifest="$run_dir/manifest.json"
tmp_manifest="$run_dir/manifest.json.tmp"
internal_json="$run_dir/nix-internal-json.ndjson"
warmup_internal_json="$run_dir/warmup-nix-internal-json.ndjson"
pre_state="$run_dir/pre_state.json"
post_state="$run_dir/post_state.json"
cache_condition={shlex.quote(cache_condition)}

if [[ -e "$manifest" ]]; then
  echo "refusing to overwrite existing $manifest" >&2
  exit 2
fi

snapshot_state() {{
  python - "$1" <<'PY'
import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

def read(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None

def run(args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None

payload = {{
    "host": socket.gethostname(),
    "cwd": os.getcwd(),
    "kernel_release": platform.release(),
    "boot_id": read("/proc/sys/kernel/random/boot_id"),
    "system_generation": os.path.realpath("/run/current-system") if Path("/run/current-system").exists() else None,
    "cpu_governor": next((read(p) for p in sorted(Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_governor")) if read(p)), None),
    "power_profile": run(["powerprofilesctl", "get"]),
    "git": {{
        "root": run(["git", "rev-parse", "--show-toplevel"]),
        "head": run(["git", "rev-parse", "HEAD"]),
        "branch": run(["git", "branch", "--show-current"]),
        "dirty": (run(["git", "status", "--porcelain"]) or "") != "",
    }},
}}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
}}

snapshot_state "$pre_state"
prepare_cache_condition() {{
  case "$cache_condition" in
    warm)
      {command_literal} 2> "$warmup_internal_json" || true
      ;;
    cold)
      : > "$warmup_internal_json"
      ;;
    *)
      echo "unknown cache_condition: $cache_condition" >&2
      exit 2
      ;;
  esac
}}

prepare_cache_condition
started_at="$(date --utc --iso-8601=ns)"
monotonic_started_ns="$(python - <<'PY'
import time
print(time.monotonic_ns())
PY
)"

set +e
{command_literal} 2> "$internal_json"
exit_status="$?"
set -e

ended_at="$(date --utc --iso-8601=ns)"
monotonic_ended_ns="$(python - <<'PY'
import time
print(time.monotonic_ns())
PY
)"
snapshot_state "$post_state"
export started_at ended_at monotonic_started_ns monotonic_ended_ns exit_status

python - "$template" "$tmp_manifest" "$pre_state" "$post_state" <<'PY'
import hashlib
import json
import os
import platform
import socket
import sys
from pathlib import Path

template, output, pre_state_path, post_state_path = map(Path, sys.argv[1:])
payload = json.loads(template.read_text(encoding="utf-8"))
pre_state = json.loads(pre_state_path.read_text(encoding="utf-8"))
post_state = json.loads(post_state_path.read_text(encoding="utf-8"))
exit_status = int(os.environ["exit_status"])
command = json.loads({command_json!r})
selected = payload.get("planned_treatment", {{}}).get("selected_run", {{}})
cache_condition = selected.get("cache_condition")
env_digest = {{
    key: "sha256:" + hashlib.sha256(os.environ.get(key, "").encode()).hexdigest()
    for key in ("PATH", "NIX_PATH", "NIX_CONFIG", "NIX_PROFILES")
}}

payload["schema"] = "lynchpin.machine_experiment.run.v1"
payload.pop("template_status", None)
payload["host"] = socket.gethostname()
payload["command"] = command
payload["cwd"] = os.getcwd()
payload["started_at"] = os.environ["started_at"]
payload["ended_at"] = os.environ["ended_at"]
payload["monotonic_started_ns"] = int(os.environ["monotonic_started_ns"])
payload["monotonic_ended_ns"] = int(os.environ["monotonic_ended_ns"])
payload["exit_status"] = exit_status
payload["execution_outcome"] = {{
    "status": "success" if exit_status == 0 else "failure",
    "timeout_s": None,
    "censored": False,
    "retry_attempt": 1,
    "warmup_discarded": cache_condition == "warm",
    "partial_output": False,
}}
payload["measurement_context"] = {{
    "host_boot_id": pre_state.get("boot_id"),
    "system_generation": pre_state.get("system_generation"),
    "kernel_release": platform.release(),
    "cpu_governor": pre_state.get("cpu_governor"),
    "power_profile": pre_state.get("power_profile"),
    "thermal_zone_policy": "observed",
    "cache_conditioning_policy": {{
        "cache_condition": cache_condition,
        "warm": "unmeasured priming invocation writes warmup-nix-internal-json.ndjson before measured timing",
        "cold": "no deliberate priming invocation; local Nix store state is recorded but not destructively cleared",
        "warmup_capture_path": str(template.parent / "warmup-nix-internal-json.ndjson"),
    }},
    "env_digest": env_digest,
}}
payload["git"] = pre_state.get("git") or {{}}
payload["pre_state"] = pre_state
payload["post_state"] = post_state
notes = list(payload.get("notes") if isinstance(payload.get("notes"), list) else [])
notes.append("manifest.json written by exported run.sh")
payload["notes"] = notes
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY

mv "$tmp_manifest" "$manifest"
echo "wrote $manifest"
exit "$exit_status"
"""


def _write_json(path: Path, payload: dict[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str, *, overwrite: bool, executable: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


__all__ = [
    "MachineBenchmarkManifestBundle",
    "MachineBenchmarkManifestGroup",
    "MachineBenchmarkRunTemplate",
    "analyze_machine_benchmark_manifest_bundle",
    "export_machine_benchmark_manifest_bundle",
    "write_machine_benchmark_manifest_bundle",
]
