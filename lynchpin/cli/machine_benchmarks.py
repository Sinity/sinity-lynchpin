"""Controlled benchmark manifest utilities."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lynchpin.analysis.machine.benchmark_manifest_bundle import (
    analyze_machine_benchmark_manifest_bundle,
    export_machine_benchmark_manifest_bundle,
)
from lynchpin.analysis.machine.controlled_benchmarks import validate_executed_benchmark_manifest
from lynchpin.core.io import resolve_analysis_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare controlled benchmark manifests without executing them")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="write ready benchmark plan templates to a directory")
    export.add_argument("--plans", type=Path, default=Path(resolve_analysis_path("machine_benchmark_plans.json")))
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--limit", type=int, default=10)
    export.add_argument("--overwrite", action="store_true")
    export.add_argument("--no-runner", action="store_true", help="write manifests only, without per-run run.sh handoff scripts")
    export.add_argument("--json", action="store_true", help="print written paths as JSON")
    validate = sub.add_parser("validate", help="validate completed benchmark manifest.json files")
    validate.add_argument("paths", type=Path, nargs="+", help="manifest files or directories containing manifest.json")
    validate.add_argument("--require-file-refs", action="store_true")
    validate.add_argument("--json", action="store_true", help="print validation rows as JSON")
    args = parser.parse_args(argv)

    if args.command == "export":
        bundle = analyze_machine_benchmark_manifest_bundle(plans_path=args.plans, limit=args.limit)
        written = export_machine_benchmark_manifest_bundle(
            bundle,
            args.output,
            overwrite=args.overwrite,
            write_runner=not args.no_runner,
        )
        if args.json:
            sys.stdout.write(json.dumps([str(path) for path in written], indent=2, sort_keys=True) + "\n")
        else:
            for path in written:
                sys.stdout.write(f"{path}\n")
        return 0
    if args.command == "validate":
        rows = [_validate_manifest(path, require_file_refs=args.require_file_refs) for path in _manifest_paths(args.paths)]
        if args.json:
            sys.stdout.write(json.dumps(rows, indent=2, sort_keys=True) + "\n")
        else:
            for row in rows:
                status = "valid" if row["valid"] else "invalid"
                sys.stdout.write(f"{status}\t{row['path']}\n")
                for issue in row["issues"]:
                    sys.stdout.write(f"  issue: {issue}\n")
                for warning in row["warnings"]:
                    sys.stdout.write(f"  warning: {warning}\n")
        return 0 if rows and all(row["valid"] for row in rows) else 1
    return 2


def _manifest_paths(paths: list[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in paths:
        if path.is_dir():
            result.extend(sorted(path.rglob("manifest.json")))
        else:
            result.append(path)
    return tuple(result)


def _validate_manifest(path: Path, *, require_file_refs: bool) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "valid": False,
            "issues": [f"cannot read manifest JSON: {exc}"],
            "warnings": [],
        }
    if not isinstance(payload, dict):
        return {
            "path": str(path),
            "valid": False,
            "issues": ["manifest root must be an object"],
            "warnings": [],
        }
    validation = validate_executed_benchmark_manifest(
        payload,
        manifest_path=path,
        require_file_refs=require_file_refs,
    )
    return {"path": str(path), **validation.to_dict()}


if __name__ == "__main__":
    raise SystemExit(main())
