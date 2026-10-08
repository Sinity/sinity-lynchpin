"""Canonical Chisel command, including legacy flag compatibility."""

from __future__ import annotations

import argparse
import json
import sys
import subprocess
from dataclasses import asdict
from pathlib import Path

from lynchpin.sources.chisel_options import BuildOptions, DATASETS, DEFAULT_PROJECTS, PROFILES


def _pairs(values: list[str], separator: str) -> tuple[tuple[str, str], ...]:
    rows = []
    for value in values:
        project, found, item = value.partition(separator)
        if not found or not project or not item:
            raise ValueError(f"expected PROJECT{separator}VALUE: {value}")
        rows.append((project, item))
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    from lynchpin.analysis.projects.chisel import build_chisel_bundles
    from lynchpin.sources.chisel import REPO_PLANS
    from lynchpin.sources.chisel_snapshots import resolve_snapshot

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "layout":
        from lynchpin.sources.code_snapshots import snapshot_layout_plan
        parser = argparse.ArgumentParser(prog="chisel layout")
        parser.add_argument("--input-root", type=Path, required=True)
        args = parser.parse_args(argv[1:])
        print(json.dumps(snapshot_layout_plan(args.input_root), indent=2))
        return 0
    if argv and argv[0] == "complete":
        word = argv[1] if len(argv) > 1 else ""
        if "=" in word:
            project, prefix = word.split("=", 1)
            if project in REPO_PLANS:
                result = subprocess.run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
                    cwd=REPO_PLANS[project].path, check=True, capture_output=True, text=True)
                print("\n".join(f"{project}={ref}" for ref in result.stdout.splitlines() if ref.startswith(prefix)))
        else:
            choices = [*REPO_PLANS, *PROFILES, *DATASETS, "list", "inspect", "validate", "render",
                       "--all", "--exclude", "--ref", "--task-root", "--target", "--profile", "--dataset",
                       "--exclude-dataset", "--output", "--workers", "--refresh", "--plan", "--xml", "--sqlite",
                       "--context-days", "--context-limit", "--context-bytes", "--attachment-bytes", "--attachment-layout",
                       "--verbose"]
            print("\n".join(value for value in choices if value.startswith(word)))
        return 0
    if argv and argv[0] in {"inspect", "validate", "render"}:
        command = argv.pop(0)
        parser = argparse.ArgumentParser(prog=f"chisel {command}")
        parser.add_argument("package", type=Path)
        parser.add_argument("--format", choices=["xml"], default="xml")
        args = parser.parse_args(argv)
        if command == "validate":
            from lynchpin.sources.chisel_publication import _validate_project

            if (args.package / "attachments").is_dir():
                from lynchpin.sources.chisel_attachments import digest

                for path in (args.package / "attachments").glob("*.json"):
                    for row in json.loads(path.read_text())["contents"]:
                        target = (args.package / row["path"]).resolve()
                        if not target.is_relative_to(args.package.resolve()):
                            raise ValueError("unsafe attachment path")
                        if target.stat().st_size != row["bytes"] or digest(target) != row["sha256"]:
                            raise ValueError(f"attachment hash mismatch: {row['path']}")
            elif (args.package / "portfolio.json").exists():
                locations = args.package / "locations.json"
                paths = json.loads(locations.read_text())["projects"] if locations.exists() else {}
                for project in json.loads((args.package / "portfolio.json").read_text())["projects"]:
                    name = project["project"]
                    _validate_project(args.package, name, project_dir=Path(paths[name]) if name in paths else None)
            else:
                name = json.loads((args.package / "capture.json").read_text())["project"]
                _validate_project(args.package.parent, name, project_dir=args.package)
            print("Package hashes verified")
        elif command == "inspect":
            path = args.package / "portfolio.json"
            if not path.exists():
                path = args.package / "capture.json"
            print(path.read_text())
        else:
            import xml.etree.ElementTree as ET

            root = ET.Element("chisel", package=str(args.package))
            for path in sorted((args.package / "source").rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    try:
                        content = data.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    if any(ord(char) < 32 and char not in "\t\n\r" for char in content):
                        continue
                    ET.SubElement(root, "file", path=path.relative_to(args.package / "source").as_posix()).text = content
            print(ET.tostring(root, encoding="unicode"))
        return 0
    parser = argparse.ArgumentParser(prog="chisel", description="Build locally pinned evidence packages.")
    parser.add_argument("selection", nargs="*")
    parser.add_argument("--projects", default="", help="Legacy whitespace-separated selection")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--ref", action="append", default=[], metavar="PROJECT=REF")
    parser.add_argument("--task-root", action="append", default=[], metavar="PROJECT:TASK")
    parser.add_argument("--target", choices=["default", "worktree"], default="default")
    parser.add_argument("--profile", choices=PROFILES, default="review")
    parser.add_argument("--dataset", action="append", choices=DATASETS, default=[])
    parser.add_argument("--exclude-dataset", action="append", choices=DATASETS, default=[])
    parser.add_argument("--context-days", type=int, default=30)
    parser.add_argument("--context-limit", type=int, default=200)
    parser.add_argument("--context-bytes", type=int, default=2_000_000)
    parser.add_argument("--attachment-bytes", type=int, default=500_000_000)
    parser.add_argument("--attachment-layout", choices=["auto", "project", "dataset"], default="auto")
    parser.add_argument("--output-root", "--output", type=Path)
    parser.add_argument("--max-workers", "--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--events", help="Append machine-readable stage events to this JSONL file")
    parser.add_argument("--xml", action="store_true")
    parser.add_argument("--sqlite", action="store_true")
    parser.add_argument("--verbose", action="store_true",
                        help="Also print library warnings, which are otherwise only logged")
    args = parser.parse_args(argv)
    if args.list or args.selection == ["list"]:
        print("\n".join(f"{name}\t{plan.path}" for name, plan in REPO_PLANS.items()))
        return 0
    names = list(dict.fromkeys(args.selection + args.projects.split()))
    if not names:
        names = list(REPO_PLANS if args.all else DEFAULT_PROJECTS)
    names = [name for name in names if name not in args.exclude]
    unknown = set(names) - REPO_PLANS.keys()
    if unknown or not names:
        parser.error(f"invalid or empty project selection: {sorted(unknown)}")
    try:
        options = BuildOptions(refresh=args.refresh, target=args.target,
            refs=_pairs(args.ref, "="), task_roots=_pairs(args.task_root, ":"),
            datasets=tuple(x for x in DATASETS if x in set(PROFILES[args.profile]) | set(args.dataset)
                           and x not in args.exclude_dataset),
            context_days=args.context_days, context_limit=args.context_limit,
            context_bytes=args.context_bytes, attachment_bytes=args.attachment_bytes,
            attachment_layout=args.attachment_layout, events=args.events, xml=args.xml, sqlite=args.sqlite)
        if set(p for p, _ in (*options.refs, *options.task_roots)) - set(names):
            raise ValueError("refs and task roots must name selected projects")
    except ValueError as exc:
        parser.error(str(exc))
    if args.plan:
        from lynchpin.sources.chisel import _default_output_root

        output = args.output_root or _default_output_root()
        rows = []
        for name in names:
            plan = REPO_PLANS[name]
            try:
                identity = resolve_snapshot(plan.path)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                identity = {"available": False, "reason": str(exc)}
            manifest = output / name / f"{name}-manifest.json"
            prior = json.loads(manifest.read_text()) if manifest.exists() else None
            prior_capture = output / name / "capture.json"
            previous_identity = json.loads(prior_capture.read_text()) if prior_capture.exists() else {}
            estimated = sum(int(row.get("bytes", row.get("size_bytes", 0))) for row in prior.get("artifacts", [])) if prior else None
            rows.append({"project": name, "primary": identity,
                         "candidates": [resolve_snapshot(plan.path, ref) for project, ref in options.refs if project == name],
                         "datasets": [{"name": dataset, "availability": "local owner queried during build" if dataset in {"execution", "context", "trackers"} else "repository available" if plan.path.exists() else "unavailable"} for dataset in options.datasets],
                         "estimated_bytes": estimated, "estimate_coverage": "prior package size; current inputs may differ" if prior else "no prior package",
                         "cache_reuse": {"prior_capture": previous_identity.get("snapshot_id"),
                             "same_revision": previous_identity.get("revision") == identity.get("revision") if prior else None,
                             "validation": "source and owner hashes checked during build"}})
        print(json.dumps({"options": asdict(options), "projects": rows}, indent=2))
        return 0
    result = build_chisel_bundles(project_names=names, output_root=args.output_root,
                                  max_workers=args.max_workers, options=options, verbose=args.verbose)
    return int(not result.get("published", all(p.get("status") == "generated" for p in result.get("projects", {}).values())))


if __name__ == "__main__":
    raise SystemExit(main())
