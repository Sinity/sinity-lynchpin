"""CLI entrypoints for project analysis materializers."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Sequence

from ..active.ai_attribution import run_active_ai_attribution
from ..active.git_facts import run_active_git_facts
from ..active.snapshot import run_active_project_snapshot
from ..change.commit_capsules import run_active_commit_semantics
from ..change.work_packages import run_active_work_packages
from ..code_index.symbol_changes import run_active_symbol_changes
from ..code_index.symbol_diffs import run_active_symbol_diffs
from ..code_index.symbol_index import run_active_symbol_index
from ..frontier.ci_health import run_active_ci_health
from ..frontier.github_frontier import run_active_github_frontier
from ..interpretation.python_dependency_hygiene import run_active_python_dependency_hygiene
from ..interpretation.rust_dependency_hygiene import run_active_rust_dependency_hygiene
from ..interpretation.semantic_static_findings import run_active_semantic_static_findings
from ..interpretation.shape import run_active_guardrails, run_active_hotspots
from ..interpretation.structural_findings import run_active_structural_findings
from ..interpretation.velocity_windows import run_project_velocity_windows
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

    active_git = subparsers.add_parser(
        "active-git-facts",
        help="Build active-project default-branch commit and file-change facts.",
    )
    _add_active_git_facts_args(active_git)
    active_work = subparsers.add_parser(
        "active-work-packages",
        help="Build active-project commit-rooted work packages.",
    )
    _add_active_work_packages_args(active_work)
    velocity_windows = subparsers.add_parser(
        "project-velocity-windows",
        help="Build project velocity windows over active facts and correlations.",
    )
    _add_project_velocity_windows_args(velocity_windows)

    return parser


def add_analysis_commands(subparsers: argparse._SubParsersAction) -> None:
    cmd_cross = subparsers.add_parser('cross', help='Cross-project metric analysis')
    cmd_cross.add_argument('--base_dir', default='/realm/project')
    cmd_cross.add_argument('--out', default=None)

    cmd_active_snapshot = subparsers.add_parser(
        "active-project-snapshot",
        help="Build active-project tracked-file and default-branch git facts",
    )
    cmd_active_snapshot.add_argument("--start", type=_parse_date, default=None)
    cmd_active_snapshot.add_argument("--end", type=_parse_date, default=None)
    cmd_active_snapshot.add_argument("--project", action="append", default=[])
    cmd_active_snapshot.add_argument("--out", default=None)

    cmd_active_git = subparsers.add_parser(
        "active-git-facts",
        help="Build active-project default-branch commit and file-change facts",
    )
    _add_active_git_facts_args(cmd_active_git)

    cmd_active_work = subparsers.add_parser(
        "active-work-packages",
        help="Build active-project commit-rooted work packages",
    )
    _add_active_work_packages_args(cmd_active_work)

    cmd_velocity_windows = subparsers.add_parser(
        "project-velocity-windows",
        help="Build project velocity windows over active facts and correlations",
    )
    _add_project_velocity_windows_args(cmd_velocity_windows)

    cmd_hotspots = subparsers.add_parser(
        "active-code-hotspots",
        help="Build active-project code hotspot ranking from file-change facts",
    )
    _add_window_with_snapshot_args(cmd_hotspots)
    cmd_hotspots.add_argument("--file-changes", default=None)
    cmd_hotspots.add_argument("--out", default=None)

    cmd_guardrails = subparsers.add_parser(
        "active-quality-guardrails",
        help="Build active-project quality guardrail movement from file-change facts",
    )
    _add_window_with_snapshot_args(cmd_guardrails)
    cmd_guardrails.add_argument("--file-changes", default=None)
    cmd_guardrails.add_argument("--out", default=None)

    cmd_structural = subparsers.add_parser(
        "active-structural-findings",
        help="Run ast-grep structural findings filtered by recent file changes",
    )
    _add_window_with_snapshot_args(cmd_structural)
    cmd_structural.add_argument("--file-changes", default=None)
    cmd_structural.add_argument("--out", default=None)

    cmd_semantic_static = subparsers.add_parser(
        "active-semantic-static-findings",
        help="Run curated semgrep privacy rules over the lynchpin repo",
    )
    _add_window_with_snapshot_args(cmd_semantic_static)
    cmd_semantic_static.add_argument("--file-changes", default=None)
    cmd_semantic_static.add_argument("--out", default=None)

    cmd_rust_hygiene = subparsers.add_parser(
        "active-rust-dependency-hygiene",
        help="Run cargo-machete (and optionally cargo-geiger) over Rust workspaces",
    )
    _add_window_with_snapshot_args(cmd_rust_hygiene)
    cmd_rust_hygiene.add_argument("--include-geiger", action="store_true")
    cmd_rust_hygiene.add_argument("--out", default=None)

    cmd_py_hygiene = subparsers.add_parser(
        "active-python-dependency-hygiene",
        help="Run pip-audit against active Python projects, marking advisories direct/transitive",
    )
    _add_window_with_snapshot_args(cmd_py_hygiene)
    cmd_py_hygiene.add_argument("--import-graph", default=None)
    cmd_py_hygiene.add_argument("--out", default=None)

    cmd_symbol_index = subparsers.add_parser(
        "active-symbol-index",
        help="Build tree-sitter symbol index across active project checkouts",
    )
    cmd_symbol_index.add_argument("--project", action="append", default=[])
    cmd_symbol_index.add_argument("--out", default=None)

    cmd_symbol_changes = subparsers.add_parser(
        "active-symbol-changes",
        help="Correlate symbol index with file-change facts (path-level)",
    )
    cmd_symbol_changes.add_argument("--start", type=_parse_date, default=None)
    cmd_symbol_changes.add_argument("--end", type=_parse_date, default=None)
    cmd_symbol_changes.add_argument("--project", action="append", default=[])
    cmd_symbol_changes.add_argument("--symbol-index", default=None)
    cmd_symbol_changes.add_argument("--file-changes", default=None)
    cmd_symbol_changes.add_argument("--out", default=None)

    cmd_symbol_diffs = subparsers.add_parser(
        "active-symbol-diffs",
        help="Line-range symbol diff intersection (runs git show per commit)",
    )
    cmd_symbol_diffs.add_argument("--start", type=_parse_date, default=None)
    cmd_symbol_diffs.add_argument("--end", type=_parse_date, default=None)
    cmd_symbol_diffs.add_argument("--project", action="append", default=[])
    cmd_symbol_diffs.add_argument("--commit-facts", default=None)
    cmd_symbol_diffs.add_argument("--symbol-index", default=None)
    cmd_symbol_diffs.add_argument("--snapshot", default=None)
    cmd_symbol_diffs.add_argument("--out", default=None)

    cmd_commit_semantics = subparsers.add_parser(
        "active-commit-semantics",
        help="Build commit-rooted semantic capsules from active commit facts",
    )
    cmd_commit_semantics.add_argument("--start", type=_parse_date, default=None)
    cmd_commit_semantics.add_argument("--end", type=_parse_date, default=None)
    cmd_commit_semantics.add_argument("--project", action="append", default=[])
    cmd_commit_semantics.add_argument("--commit-facts", default=None)
    cmd_commit_semantics.add_argument("--out", default=None)

    cmd_ai_attribution = subparsers.add_parser(
        "active-ai-attribution",
        help="Backfill AI co-authorship attribution by joining commits with polylogue sessions",
    )
    cmd_ai_attribution.add_argument("--start", type=_parse_date, default=None)
    cmd_ai_attribution.add_argument("--end", type=_parse_date, default=None)
    cmd_ai_attribution.add_argument("--project", action="append", default=[])
    cmd_ai_attribution.add_argument("--commit-facts", default=None)
    cmd_ai_attribution.add_argument("--out", default=None)

    cmd_github_frontier = subparsers.add_parser(
        "active-github-frontier",
        help="Build active-project GitHub frontier (issues/PRs) — requires gh on PATH",
    )
    _add_window_with_snapshot_args(cmd_github_frontier)
    cmd_github_frontier.add_argument("--work-packages", default=None)
    cmd_github_frontier.add_argument("--out", default=None)

    cmd_ci_health = subparsers.add_parser(
        "active-ci-health",
        help="Static .github/workflows parsing; optional gh api run telemetry",
    )
    _add_window_with_snapshot_args(cmd_ci_health)
    cmd_ci_health.add_argument("--include-runs", action="store_true",
                                help="Network: query gh api for last 30d of run history")
    cmd_ci_health.add_argument("--out", default=None)


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

    if args.command == "active-git-facts":
        commit_out = args.commit_out or resolve_analysis_path("active_commit_facts.json")
        file_out = args.file_out or resolve_analysis_path("active_file_change_facts.json")
        run_active_git_facts(
            commit_out,
            file_out,
            start=args.start,
            end=args.end,
            projects=args.project,
        )
        return 0

    if args.command == "active-work-packages":
        out = args.out or resolve_analysis_path("active_work_packages.json")
        run_active_work_packages(
            out,
            start=args.start,
            end=args.end,
            projects=args.project,
        )
        return 0

    if args.command == "project-velocity-windows":
        out = args.out or resolve_analysis_path("project_velocity_windows.json")
        run_project_velocity_windows(
            out,
            start=args.start,
            end=args.end,
            projects=args.project,
            commit_facts_file=args.commit_facts or resolve_analysis_path("active_commit_facts.json"),
            work_packages_file=args.work_packages or resolve_analysis_path("active_work_packages.json"),
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

    if args.command == "active-project-snapshot":
        out = args.out or resolve_analysis_path("active_project_snapshot.json")
        run_active_project_snapshot(
            out,
            start=args.start,
            end=args.end,
            projects=args.project,
        )
        return 0

    if args.command == "active-git-facts":
        commit_out = args.commit_out or resolve_analysis_path("active_commit_facts.json")
        file_out = args.file_out or resolve_analysis_path("active_file_change_facts.json")
        run_active_git_facts(
            commit_out,
            file_out,
            start=args.start,
            end=args.end,
            projects=args.project,
        )
        return 0

    if args.command == "active-work-packages":
        out = args.out or resolve_analysis_path("active_work_packages.json")
        run_active_work_packages(
            out,
            start=args.start,
            end=args.end,
            projects=args.project,
        )
        return 0

    if args.command == "project-velocity-windows":
        out = args.out or resolve_analysis_path("project_velocity_windows.json")
        run_project_velocity_windows(
            out,
            start=args.start,
            end=args.end,
            projects=args.project,
            commit_facts_file=args.commit_facts or resolve_analysis_path("active_commit_facts.json"),
            work_packages_file=args.work_packages or resolve_analysis_path("active_work_packages.json"),
        )
        return 0

    if args.command == "active-code-hotspots":
        out = args.out or resolve_analysis_path("active_code_hotspots.json")
        run_active_hotspots(
            out,
            start=args.start, end=args.end, projects=args.project,
            file_changes_file=args.file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )
        return 0

    if args.command == "active-quality-guardrails":
        out = args.out or resolve_analysis_path("active_quality_guardrails.json")
        run_active_guardrails(
            out,
            start=args.start, end=args.end, projects=args.project,
            file_changes_file=args.file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )
        return 0

    if args.command == "active-structural-findings":
        out = args.out or resolve_analysis_path("active_structural_findings.json")
        run_active_structural_findings(
            out,
            start=args.start, end=args.end, projects=args.project,
            file_changes_file=args.file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )
        return 0

    if args.command == "active-semantic-static-findings":
        out = args.out or resolve_analysis_path("active_semantic_static_findings.json")
        run_active_semantic_static_findings(
            out,
            start=args.start, end=args.end, projects=args.project,
            file_changes_file=args.file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )
        return 0

    if args.command == "active-rust-dependency-hygiene":
        out = args.out or resolve_analysis_path("active_rust_dependency_hygiene.json")
        run_active_rust_dependency_hygiene(
            out,
            start=args.start, end=args.end, projects=args.project,
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
            include_geiger=args.include_geiger,
        )
        return 0

    if args.command == "active-python-dependency-hygiene":
        out = args.out or resolve_analysis_path("active_python_dependency_hygiene.json")
        run_active_python_dependency_hygiene(
            out,
            start=args.start, end=args.end, projects=args.project,
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
            import_graph_file=args.import_graph or resolve_analysis_path("active_python_import_graph.json"),
        )
        return 0

    if args.command == "active-symbol-index":
        out = args.out or resolve_analysis_path("active_symbol_index.json")
        run_active_symbol_index(out, projects=args.project)
        return 0

    if args.command == "active-symbol-changes":
        out = args.out or resolve_analysis_path("active_symbol_changes.json")
        run_active_symbol_changes(
            out,
            start=args.start, end=args.end, projects=args.project,
            symbol_index_file=args.symbol_index or resolve_analysis_path("active_symbol_index.json"),
            file_changes_file=args.file_changes or resolve_analysis_path("active_file_change_facts.json"),
        )
        return 0

    if args.command == "active-symbol-diffs":
        out = args.out or resolve_analysis_path("active_symbol_diffs.json")
        run_active_symbol_diffs(
            out,
            start=args.start, end=args.end, projects=args.project,
            commit_facts_file=args.commit_facts or resolve_analysis_path("active_commit_facts.json"),
            symbol_index_file=args.symbol_index or resolve_analysis_path("active_symbol_index.json"),
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )
        return 0

    if args.command == "active-commit-semantics":
        out = args.out or resolve_analysis_path("active_commit_semantics.json")
        run_active_commit_semantics(
            out,
            start=args.start, end=args.end, projects=args.project,
        )
        return 0

    if args.command == "active-ai-attribution":
        out = args.out or resolve_analysis_path("active_ai_attribution.json")
        run_active_ai_attribution(
            out,
            start=args.start, end=args.end, projects=args.project,
        )
        return 0

    if args.command == "active-github-frontier":
        out = args.out or resolve_analysis_path("active_github_frontier.json")
        run_active_github_frontier(
            out,
            start=args.start, end=args.end, projects=args.project,
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
            work_packages_file=args.work_packages or resolve_analysis_path("active_work_packages.json"),
        )
        return 0

    if args.command == "active-ci-health":
        out = args.out or resolve_analysis_path("active_ci_health.json")
        run_active_ci_health(
            out,
            start=args.start, end=args.end, projects=args.project,
            snapshot_file=args.snapshot or resolve_analysis_path("active_project_snapshot.json"),
            include_runs=args.include_runs,
        )
        return 0

    return None


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date: {value!r}") from exc


def _add_active_git_facts_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--commit-out", default=None)
    parser.add_argument("--file-out", default=None)


def _add_active_work_packages_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--commit-facts", default=None)
    parser.add_argument("--out", default=None)


def _add_project_velocity_windows_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--commit-facts", default=None)
    parser.add_argument("--work-packages", default=None)
    parser.add_argument("--out", default=None)


def _add_window_with_snapshot_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--snapshot", default=None)
