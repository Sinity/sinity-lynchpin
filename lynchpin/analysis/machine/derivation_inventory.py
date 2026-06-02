"""Fixed Nix derivation inventory for controlled benchmark planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
from typing import Any, Callable

from lynchpin.core.config import get_config
from lynchpin.core.cache import files_signature
from lynchpin.core.io import load_json_if_exists, save_json


NixEval = Callable[[list[str], Path], str]


@dataclass(frozen=True)
class MachineDerivationTarget:
    project: str
    repo_path: str
    flake_ref: str
    attr: str
    drv_path: str | None
    store_path: str | None
    eval_status: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineDerivationInventory:
    generated_for: dict[str, Any]
    target_count: int
    ready_target_count: int
    targets: list[MachineDerivationTarget]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_derivation_inventory(
    *,
    start: date | None = None,
    end: date | None = None,
    roots: tuple[tuple[str, Path], ...] | None = None,
    system: str | None = None,
    evaluator: NixEval | None = None,
) -> MachineDerivationInventory:
    """Evaluate flake package derivation paths without building packages."""
    roots = roots or _default_roots()
    system = system or _nix_system()
    evaluator = evaluator or _nix_eval
    targets: list[MachineDerivationTarget] = []
    caveats: list[str] = []
    for project, root in roots:
        if not (root / "flake.nix").exists():
            caveats.append(f"{project}: no flake.nix at {root}")
            continue
        flake_base = _flake_base(root)
        if dirty := _git_dirty(root):
            caveats.append(
                f"{project}: evaluating committed HEAD for reproducible derivations; "
                f"{len(dirty.splitlines())} dirty worktree paths are excluded"
            )
        outputs = _package_outputs(root, system=system, evaluator=evaluator, flake_base=flake_base)
        if outputs:
            for attr, output in outputs.items():
                if attr in {"default", "pg_jsonschema"}:
                    continue
                targets.append(_target_from_output(
                    project,
                    root,
                    system=system,
                    attr=attr,
                    output=output,
                    flake_base=flake_base,
                ))
            continue
        attrs = _package_attrs(root, system=system, evaluator=evaluator, flake_base=flake_base)
        if not attrs:
            caveats.append(f"{project}: no package outputs discovered for {system}")
        for attr in attrs:
            if attr in {"default", "pg_jsonschema"}:
                continue
            targets.append(_target(project, root, system=system, attr=attr, evaluator=evaluator, flake_base=flake_base))
    targets.sort(key=lambda row: (row.project, row.attr))
    return MachineDerivationInventory(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "system": system,
            "method": "nix eval .#packages.<system>.<attr>.drvPath; no builds executed",
        },
        target_count=len(targets),
        ready_target_count=sum(1 for row in targets if row.eval_status == "ready"),
        targets=targets,
        caveats=caveats,
    )


def write_machine_derivation_inventory(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    roots: tuple[tuple[str, Path], ...] | None = None,
    system: str | None = None,
    evaluator: NixEval | None = None,
) -> MachineDerivationInventory:
    resolved_roots = roots or _default_roots()
    resolved_system = system or _nix_system()
    input_signature = _input_signature(resolved_roots)
    if evaluator is None:
        existing = load_json_if_exists(out)
        if (
            isinstance(existing, dict)
            and existing.get("generated_for", {}).get("system") == resolved_system
            and existing.get("generated_for", {}).get("input_signature") == input_signature
        ):
            return _inventory_from_payload(existing)
    analysis = analyze_machine_derivation_inventory(
        start=start,
        end=end,
        roots=resolved_roots,
        system=resolved_system,
        evaluator=evaluator,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    payload["generated_for"]["input_signature"] = input_signature
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def derivations_from_inventory(payload: dict[str, Any] | None, *, project: str | None = None) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict):
        return ()
    rows = payload.get("targets")
    if not isinstance(rows, list):
        return ()
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("eval_status") != "ready":
            continue
        if project is not None and row.get("project") not in {project, "sinity-lynchpin"}:
            continue
        drv_path = row.get("drv_path")
        if not drv_path:
            continue
        result.append({
            "project": row.get("project"),
            "name": row.get("attr"),
            "drv_path": drv_path,
            "store_path": row.get("store_path"),
            "flake_ref": row.get("flake_ref"),
        })
    return tuple(result)


def derivations_for_candidate(payload: dict[str, Any] | None, candidate: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return the narrow fixed derivation set for one benchmark candidate.

    The inventory is intentionally broad. A benchmark, however, needs a fixed
    derivation set tied to the workload under test; otherwise the run manifest
    can silently rotate across unrelated package outputs and stop estimating a
    single treatment effect.
    """
    if not isinstance(payload, dict):
        return ()
    project = _candidate_project(candidate)
    attrs = _candidate_attrs(candidate, project=project)
    rows = _ready_target_rows(payload)
    selected = [
        row for row in rows
        if (project is None or row.get("project") == project)
        and (not attrs or row.get("attr") in attrs)
    ]
    if not selected and project is not None:
        selected = [row for row in rows if row.get("project") == project]
    return tuple(_derivation_payload(row) for row in selected if _derivation_payload(row))


def _package_attrs(root: Path, *, system: str, evaluator: NixEval, flake_base: str) -> tuple[str, ...]:
    try:
        raw = evaluator(["nix", "eval", "--json", f"{flake_base}#packages.{system}", "--apply", "builtins.attrNames"], root)
        decoded = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return ()
    return tuple(str(item) for item in decoded) if isinstance(decoded, list) else ()


def _default_roots() -> tuple[tuple[str, Path], ...]:
    cfg = get_config()
    return (
        ("sinex", Path("/realm/project/sinex")),
        ("polylogue", cfg.polylogue_project_root),
        ("sinity-lynchpin", cfg.repo_root),
    )


def _input_signature(roots: tuple[tuple[str, Path], ...]) -> list[dict[str, Any]]:
    payload = [
        {
            "project": project,
            "root": str(root),
            "source_ref": _flake_base(root),
            "files": files_signature((root / "flake.nix", root / "flake.lock"))
            if _git(root, "rev-parse", "HEAD") is None
            else None,
            "git_head": _git(root, "rev-parse", "HEAD"),
        }
        for project, root in roots
    ]
    return json.loads(json.dumps(payload, default=str))


def _inventory_from_payload(payload: dict[str, Any]) -> MachineDerivationInventory:
    rows = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    targets = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        targets.append(
            MachineDerivationTarget(
                project=str(row.get("project") or ""),
                repo_path=str(row.get("repo_path") or ""),
                flake_ref=str(row.get("flake_ref") or ""),
                attr=str(row.get("attr") or ""),
                drv_path=_text(row.get("drv_path")),
                store_path=_text(row.get("store_path")),
                eval_status=str(row.get("eval_status") or "unknown"),
                caveats=tuple(str(item) for item in row.get("caveats", ()) if item),
            )
        )
    generated_for = payload.get("generated_for") if isinstance(payload.get("generated_for"), dict) else {}
    caveats = payload.get("caveats") if isinstance(payload.get("caveats"), list) else []
    return MachineDerivationInventory(
        generated_for=generated_for,
        target_count=int(payload.get("target_count") or len(targets)),
        ready_target_count=int(payload.get("ready_target_count") or sum(1 for row in targets if row.eval_status == "ready")),
        targets=targets,
        caveats=[str(item) for item in caveats],
    )


def _package_outputs(root: Path, *, system: str, evaluator: NixEval, flake_base: str) -> dict[str, dict[str, Any]]:
    expr = (
        "pkgs: builtins.mapAttrs "
        "(_: p: { drvPath = p.drvPath or null; outPath = p.outPath or null; }) "
        "pkgs"
    )
    try:
        raw = evaluator(["nix", "eval", "--json", f"{flake_base}#packages.{system}", "--apply", expr], root)
        decoded = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {
        str(attr): output
        for attr, output in decoded.items()
        if isinstance(output, dict)
    }


def _ready_target_rows(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    rows = payload.get("targets")
    if not isinstance(rows, list):
        return ()
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("eval_status") != "ready" or not row.get("drv_path"):
            continue
        result.append(row)
    return tuple(result)


def _derivation_payload(row: dict[str, Any]) -> dict[str, Any]:
    drv_path = row.get("drv_path")
    if not drv_path:
        return {}
    return {
        "project": row.get("project"),
        "name": row.get("attr"),
        "drv_path": drv_path,
        "store_path": row.get("store_path"),
        "flake_ref": row.get("flake_ref"),
    }


def _candidate_project(candidate: dict[str, Any]) -> str | None:
    project = _text(candidate.get("project"))
    if project is not None:
        return project
    terms = _candidate_terms(candidate)
    if "xtask-stage" in terms or "xtask.stage" in terms:
        return "sinex"
    if "lynchpin" in terms or "pytest" in terms:
        return "sinity-lynchpin"
    return None


def _candidate_attrs(candidate: dict[str, Any], *, project: str | None) -> tuple[str, ...]:
    terms = _candidate_terms(candidate)
    if project == "sinex" and ("xtask-stage" in terms or "xtask.stage" in terms or "stage.duration_s" in terms):
        return ("xtask",)
    if project == "sinity-lynchpin":
        return ("lynchpin",)
    return ()


def _candidate_terms(candidate: dict[str, Any]) -> str:
    suggested = candidate.get("suggested_benchmark_manifest")
    workload = _text(suggested.get("workload")) if isinstance(suggested, dict) else None
    parts = [
        candidate.get("project"),
        candidate.get("metric"),
        candidate.get("suspected_factor"),
        workload,
    ]
    return " ".join(str(part).lower() for part in parts if part)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _target(
    project: str,
    root: Path,
    *,
    system: str,
    attr: str,
    evaluator: NixEval,
    flake_base: str,
) -> MachineDerivationTarget:
    flake_ref = f"{flake_base}#packages.{system}.{attr}"
    caveats = []
    drv_path = None
    store_path = None
    status = "ready"
    try:
        drv_path = evaluator(["nix", "eval", "--raw", f"{flake_ref}.drvPath"], root).strip() or None
    except (subprocess.CalledProcessError, OSError) as exc:
        status = "eval_error"
        caveats.append(f"drvPath eval failed: {exc}")
    if drv_path is not None:
        try:
            store_path = evaluator(["nix", "eval", "--raw", f"{flake_ref}.outPath"], root).strip() or None
        except (subprocess.CalledProcessError, OSError) as exc:
            caveats.append(f"outPath eval failed: {exc}")
    return MachineDerivationTarget(
        project=project,
        repo_path=str(root),
        flake_ref=flake_ref,
        attr=attr,
        drv_path=drv_path,
        store_path=store_path,
        eval_status=status,
        caveats=tuple(caveats),
    )


def _target_from_output(
    project: str,
    root: Path,
    *,
    system: str,
    attr: str,
    output: dict[str, Any],
    flake_base: str,
) -> MachineDerivationTarget:
    drv_path = _text(output.get("drvPath"))
    store_path = _text(output.get("outPath"))
    caveats = []
    status = "ready"
    if drv_path is None:
        status = "eval_error"
        caveats.append("bulk package eval did not return drvPath")
    return MachineDerivationTarget(
        project=project,
        repo_path=str(root),
        flake_ref=f"{flake_base}#packages.{system}.{attr}",
        attr=attr,
        drv_path=drv_path,
        store_path=store_path,
        eval_status=status,
        caveats=tuple(caveats),
    )


def _nix_eval(argv: list[str], cwd: Path) -> str:
    return subprocess.check_output(argv, cwd=cwd, text=True, stderr=subprocess.STDOUT)


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def _git_dirty(root: Path) -> str | None:
    return _git(root, "status", "--porcelain")


def _flake_base(root: Path) -> str:
    head = _git(root, "rev-parse", "HEAD")
    if head is None:
        return str(root)
    return f"git+{root.resolve().as_uri()}?rev={head}"


def _nix_system() -> str:
    machine = platform.machine()
    if machine in {"x86_64", "amd64"}:
        return "x86_64-linux"
    if machine in {"aarch64", "arm64"}:
        return "aarch64-linux"
    return f"{machine}-linux"


__all__ = [
    "MachineDerivationInventory",
    "MachineDerivationTarget",
    "analyze_machine_derivation_inventory",
    "derivations_for_candidate",
    "derivations_from_inventory",
    "write_machine_derivation_inventory",
]
