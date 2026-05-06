"""CLI entrypoints for project analysis materializers."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .velocity_renderer import DEFAULT_OUTPUT, build_velocity_dashboard
from ..core.io import resolve_analysis_path


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project analysis materializers for velocity and chisel snapshots.",
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


def add_analysis_commands(subparsers: argparse._SubParsersAction) -> None:
    cmd_cross = subparsers.add_parser('cross', help='Cross-project metric analysis')
    cmd_cross.add_argument('--base_dir', default='/realm/project')
    cmd_cross.add_argument('--out', default=None)


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


def run_analysis_command(args: argparse.Namespace) -> int | None:
    if args.command == 'cross':
        from . import metrics as project_metrics

        out = args.out or resolve_analysis_path('cross_project_metrics.json')
        project_metrics.run_cross_project(args.base_dir, out)
        return 0

    return None
