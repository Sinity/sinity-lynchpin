"""CLI entrypoints for project analysis materializers."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .bundles import BUNDLE_ROOT, DEFAULT_LOGS_COUNT, build_project_bundles
from .rich_bundles import (
    DEFAULT_PATCH_COMMITS,
    DEFAULT_PATCH_WINDOW,
    DEFAULT_SUMMARY_WINDOW,
    RICH_BUNDLE_ROOT,
    build_rich_project_bundles,
)
from .velocity_renderer import DEFAULT_OUTPUT, build_velocity_dashboard


def _split_names(value: str) -> list[str] | None:
    names = [item for item in value.split() if item]
    return names or None


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def _parse_optional_int(value: str) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    return int(stripped)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project analysis materializers for velocity and context bundles.",
    )
    subparsers = parser.add_subparsers(dest="command")

    velocity = subparsers.add_parser(
        "velocity",
        help="Build the cross-project git velocity dashboard.",
    )
    velocity.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    velocity.add_argument(
        "--projects",
        default="",
        help="Whitespace-separated project names to include.",
    )
    velocity.add_argument(
        "--exclude",
        default="",
        help="Whitespace-separated project names to exclude.",
    )
    velocity.add_argument(
        "--aggregate",
        type=_parse_bool,
        default=True,
        help="Whether to include the all-projects aggregate view.",
    )

    bundles = subparsers.add_parser(
        "bundles",
        help="Build repomix-backed project context bundles.",
    )
    bundles.add_argument("--output-root", type=Path, default=BUNDLE_ROOT)
    bundles.add_argument(
        "--projects",
        default="",
        help="Whitespace-separated project names to include.",
    )
    bundles.add_argument("--logs-count", type=int, default=DEFAULT_LOGS_COUNT)
    bundles.add_argument(
        "--include-diffs",
        type=_parse_bool,
        default=False,
        help="Whether to include working-tree diffs in bundle output.",
    )
    bundles.add_argument(
        "--include-compressed",
        type=_parse_bool,
        default=True,
        help="Whether to emit the compressed bundle variant.",
    )

    rich = subparsers.add_parser(
        "rich-bundles",
        help="Build slice-aware rich project bundles with git history shards.",
    )
    rich.add_argument("--output-root", type=Path, default=RICH_BUNDLE_ROOT)
    rich.add_argument(
        "--projects",
        default="",
        help="Whitespace-separated project names to include.",
    )
    rich.add_argument("--patch-window", type=int, default=DEFAULT_PATCH_WINDOW)
    rich.add_argument("--summary-window", type=int, default=DEFAULT_SUMMARY_WINDOW)
    rich.add_argument(
        "--patch-commits",
        type=_parse_optional_int,
        default=DEFAULT_PATCH_COMMITS,
        help="Recent commit count for high-resolution patch shards; empty means full history.",
    )
    rich.add_argument(
        "--summary-commits",
        type=_parse_optional_int,
        default=None,
        help="Recent commit count for summary shards; empty means full history.",
    )

    chisel = subparsers.add_parser(
        "chisel",
        help="Build XML repomix snapshots with semantic splitting and GitHub issue commentary.",
    )
    chisel.add_argument(
        "--projects",
        default="",
        help="Whitespace-separated project names (default: all registered).",
    )
    chisel.add_argument(
        "--output-root",
        type=lambda s: Path(s) if s.strip() else None,
        default=None,
        help="Output directory (default: /realm/inbox/store/next/<timestamp>).",
    )
    chisel.add_argument(
        "--max-workers", type=int, default=4,
        help="Max parallel repos (default: 4).",
    )
    chisel.add_argument(
        "--list", action="store_true",
        help="List available project plans and exit.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "velocity":
        wrote = build_velocity_dashboard(
            output=args.output,
            project_names=_split_names(args.projects),
            exclude_names=_split_names(args.exclude),
            aggregate=args.aggregate,
            log=print,
        )
        if wrote:
            print(f"Velocity dashboard written to {args.output}")
        else:
            print("Velocity dashboard unchanged or no repositories produced history.")
        return 0

    if args.command == "bundles":
        index = build_project_bundles(
            output_root=args.output_root,
            project_names=_split_names(args.projects),
            logs_count=args.logs_count,
            include_diffs=args.include_diffs,
            include_compressed=args.include_compressed,
            log=print,
        )
        print(f"Project bundle index written to {Path(index['output_root']) / 'index.json'}")
        return 0

    if args.command == "rich-bundles":
        index = build_rich_project_bundles(
            output_root=args.output_root,
            project_names=_split_names(args.projects),
            patch_window=args.patch_window,
            summary_window=args.summary_window,
            patch_commits=args.patch_commits,
            summary_commits=args.summary_commits,
            log=print,
        )
        print(f"Rich project bundle index written to {Path(index['output_root']) / 'index.json'}")
        return 0

    if args.command == "chisel":
        from .chisel import build_chisel_bundles
        if args.list:
            from .chisel import REPO_PLANS
            print("Available chisel projects:\n")
            for name, plan in sorted(REPO_PLANS.items()):
                slices_str = ", ".join(s.name for s in plan.slices)
                print(f"  {name}")
                print(f"    path:   {plan.path}")
                print(f"    github: {plan.github_slug or '—'}")
                print(f"    slices: {slices_str}")
                if plan.extra_copy:
                    copies = ", ".join(f"{s}→{d}" for s, d in plan.extra_copy)
                    print(f"    copies: {copies}")
                print()
            return 0
        build_chisel_bundles(
            project_names=_split_names(args.projects),
            output_root=args.output_root,
            max_workers=args.max_workers,
        )
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2
