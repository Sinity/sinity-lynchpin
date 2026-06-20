"""CLI entrypoints for project analysis materializers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from ..active.ai_attribution import run_active_ai_attribution
from ..active.git_facts import run_active_git_facts
from ..active.snapshot import run_active_project_snapshot
from ..change.commit_capsules import run_active_commit_semantics
from ..change.work_packages import run_active_work_packages
from ..code_index.python_analysis import run_active_python_complexity, run_active_python_import_graph
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
from lynchpin.core.io import resolve_analysis_path


def _split_names(value: str) -> list[str] | None:
    names = [item for item in value.split() if item]
    return names or None


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise typer.BadParameter(f"invalid boolean value: {value!r}")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid date: {value!r}") from exc


def _opt_date(value: str | None) -> date | None:
    return _parse_date(value) if value else None


app = typer.Typer(
    help="Project analysis materializers for velocity and chisel snapshots.",
    no_args_is_help=True,
)


@app.command("velocity", help="Build the cross-project git velocity dashboard.")
def _velocity(
    output: Path = typer.Option(DEFAULT_OUTPUT, "--output"),
    projects: str = typer.Option("", "--projects", help="Whitespace-separated project names to include."),
    exclude: str = typer.Option("", "--exclude", help="Whitespace-separated project names to exclude."),
    aggregate: str = typer.Option("True", "--aggregate", help="Whether to include the all-projects aggregate view."),
) -> None:
    agg = _parse_bool(aggregate)
    wrote = build_velocity_dashboard(
        output=output,
        project_names=_split_names(projects),
        exclude_names=_split_names(exclude),
        aggregate=agg,
        log=print,
    )
    if wrote:
        print(f"Velocity dashboard written to {output}")
    else:
        print("Velocity dashboard unchanged or no repositories produced history.")


@app.command(
    "chisel",
    help="Build XML repomix snapshots with semantic splitting and GitHub issue commentary.",
)
def _chisel(
    projects: str = typer.Option("", "--projects", help="Whitespace-separated project names (default: all registered)."),
    output_root: str = typer.Option("", "--output-root", help="Output directory (default: /realm/data/derived/lynchpin/code-snapshots)."),
    max_workers: int = typer.Option(4, "--max-workers", help="Max parallel repos (default: 4)."),
    list_only: bool = typer.Option(False, "--list/", help="List available project plans and exit."),
) -> None:
    from .chisel import build_chisel_bundles

    if list_only:
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
        return
    output_root_path = Path(output_root) if output_root.strip() else None
    build_chisel_bundles(
        project_names=_split_names(projects),
        output_root=output_root_path,
        max_workers=max_workers,
    )


@app.command("active-git-facts", help="Build active-project default-branch commit and file-change facts.")
def _active_git_facts(
    start: str = typer.Option(None, "--start"),
    end: str = typer.Option(None, "--end"),
    project: list[str] = typer.Option(None, "--project"),
    commit_out: str | None = typer.Option(None, "--commit-out"),
    file_out: str | None = typer.Option(None, "--file-out"),
) -> None:
    commit_target = commit_out or resolve_analysis_path("active_commit_facts.json")
    file_target = file_out or resolve_analysis_path("active_file_change_facts.json")
    run_active_git_facts(
        commit_target,
        file_target,
        start=_opt_date(start),
        end=_opt_date(end),
        projects=list(project or []),
    )


@app.command("active-work-packages", help="Build active-project commit-rooted work packages.")
def _active_work_packages(
    start: str = typer.Option(None, "--start"),
    end: str = typer.Option(None, "--end"),
    project: list[str] = typer.Option(None, "--project"),
    commit_facts: str | None = typer.Option(None, "--commit-facts"),
    out: str | None = typer.Option(None, "--out"),
) -> None:
    target = out or resolve_analysis_path("active_work_packages.json")
    run_active_work_packages(
        target,
        start=_opt_date(start),
        end=_opt_date(end),
        projects=list(project or []),
    )


@app.command("project-velocity-windows", help="Build project velocity windows over active facts and correlations.")
def _project_velocity_windows(
    start: str = typer.Option(None, "--start"),
    end: str = typer.Option(None, "--end"),
    project: list[str] = typer.Option(None, "--project"),
    commit_facts: str | None = typer.Option(None, "--commit-facts"),
    work_packages: str | None = typer.Option(None, "--work-packages"),
    out: str | None = typer.Option(None, "--out"),
) -> None:
    target = out or resolve_analysis_path("project_velocity_windows.json")
    run_project_velocity_windows(
        target,
        start=_opt_date(start),
        end=_opt_date(end),
        projects=list(project or []),
        commit_facts_file=commit_facts or resolve_analysis_path("active_commit_facts.json"),
        work_packages_file=work_packages or resolve_analysis_path("active_work_packages.json"),
    )


def register_commands(parent: typer.Typer) -> None:
    @parent.command("cross", help="Cross-project metric analysis")
    def _cross(
        base_dir: str = typer.Option("/realm/project", "--base_dir"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        from . import metrics as project_metrics

        target = out or resolve_analysis_path("cross_project_metrics.json")
        project_metrics.run_cross_project(base_dir, target)  # type: ignore[no-untyped-call]

    @parent.command(
        "active-project-snapshot",
        help="Build active-project tracked-file and default-branch git facts",
    )
    def _active_project_snapshot(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_project_snapshot.json")
        run_active_project_snapshot(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            projects=list(project or []),
        )

    @parent.command(
        "active-python-complexity",
        help="Build active-project native Python complexity metrics",
    )
    def _active_python_complexity(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_python_complexity.json")
        run_active_python_complexity(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            projects=list(project or []),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )

    @parent.command(
        "active-python-import-graph",
        help="Build active-project native Python import graphs",
    )
    def _active_python_import_graph(
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_python_import_graph.json")
        run_active_python_import_graph(
            target,
            projects=list(project or []),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )

    @parent.command(
        "active-git-facts",
        help="Build active-project default-branch commit and file-change facts",
    )
    def _active_git_facts_analysis(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        commit_out: str | None = typer.Option(None, "--commit-out"),
        file_out: str | None = typer.Option(None, "--file-out"),
    ) -> None:
        commit_target = commit_out or resolve_analysis_path("active_commit_facts.json")
        file_target = file_out or resolve_analysis_path("active_file_change_facts.json")
        run_active_git_facts(
            commit_target,
            file_target,
            start=_opt_date(start),
            end=_opt_date(end),
            projects=list(project or []),
        )

    @parent.command(
        "active-work-packages",
        help="Build active-project commit-rooted work packages",
    )
    def _active_work_packages_analysis(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        commit_facts: str | None = typer.Option(None, "--commit-facts"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_work_packages.json")
        run_active_work_packages(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            projects=list(project or []),
        )

    @parent.command(
        "project-velocity-windows",
        help="Build project velocity windows over active facts and correlations",
    )
    def _project_velocity_windows_analysis(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        commit_facts: str | None = typer.Option(None, "--commit-facts"),
        work_packages: str | None = typer.Option(None, "--work-packages"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("project_velocity_windows.json")
        run_project_velocity_windows(
            target,
            start=_opt_date(start),
            end=_opt_date(end),
            projects=list(project or []),
            commit_facts_file=commit_facts or resolve_analysis_path("active_commit_facts.json"),
            work_packages_file=work_packages or resolve_analysis_path("active_work_packages.json"),
        )

    @parent.command(
        "active-code-hotspots",
        help="Build active-project code hotspot ranking from file-change facts",
    )
    def _active_code_hotspots(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        file_changes: str | None = typer.Option(None, "--file-changes"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_code_hotspots.json")
        run_active_hotspots(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            file_changes_file=file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )

    @parent.command(
        "active-quality-guardrails",
        help="Build active-project quality guardrail movement from file-change facts",
    )
    def _active_quality_guardrails(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        file_changes: str | None = typer.Option(None, "--file-changes"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_quality_guardrails.json")
        run_active_guardrails(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            file_changes_file=file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )

    @parent.command(
        "active-structural-findings",
        help="Run ast-grep structural findings filtered by recent file changes",
    )
    def _active_structural_findings(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        file_changes: str | None = typer.Option(None, "--file-changes"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_structural_findings.json")
        run_active_structural_findings(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            file_changes_file=file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )

    @parent.command(
        "active-semantic-static-findings",
        help="Run curated semgrep privacy rules over the lynchpin repo",
    )
    def _active_semantic_static_findings(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        file_changes: str | None = typer.Option(None, "--file-changes"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_semantic_static_findings.json")
        run_active_semantic_static_findings(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            file_changes_file=file_changes or resolve_analysis_path("active_file_change_facts.json"),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )

    @parent.command(
        "active-rust-dependency-hygiene",
        help="Run cargo-machete (and optionally cargo-geiger) over Rust workspaces",
    )
    def _active_rust_dependency_hygiene(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        include_geiger: bool = typer.Option(False, "--include-geiger/"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_rust_dependency_hygiene.json")
        run_active_rust_dependency_hygiene(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
            include_geiger=include_geiger,
        )

    @parent.command(
        "active-python-dependency-hygiene",
        help="Run pip-audit against active Python projects, marking advisories direct/transitive",
    )
    def _active_python_dependency_hygiene(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        import_graph: str | None = typer.Option(None, "--import-graph"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_python_dependency_hygiene.json")
        run_active_python_dependency_hygiene(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
            import_graph_file=import_graph or resolve_analysis_path("active_python_import_graph.json"),
        )

    @parent.command(
        "active-symbol-index",
        help="Build tree-sitter symbol index across active project checkouts",
    )
    def _active_symbol_index(
        project: list[str] = typer.Option(None, "--project"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_symbol_index.json")
        run_active_symbol_index(target, projects=list(project or []))

    @parent.command(
        "active-symbol-diffs",
        help="Line-range symbol diff intersection (runs git show per commit)",
    )
    def _active_symbol_diffs(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        commit_facts: str | None = typer.Option(None, "--commit-facts"),
        symbol_index: str | None = typer.Option(None, "--symbol-index"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_symbol_diffs.json")
        run_active_symbol_diffs(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            commit_facts_file=commit_facts or resolve_analysis_path("active_commit_facts.json"),
            symbol_index_file=symbol_index or resolve_analysis_path("active_symbol_index.json"),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
        )

    @parent.command(
        "active-commit-semantics",
        help="Build commit-rooted semantic capsules from active commit facts",
    )
    def _active_commit_semantics(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        commit_facts: str | None = typer.Option(None, "--commit-facts"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_commit_semantics.json")
        run_active_commit_semantics(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
        )

    @parent.command(
        "active-ai-attribution",
        help="Backfill AI co-authorship attribution by joining commits with polylogue sessions",
    )
    def _active_ai_attribution(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        commit_facts: str | None = typer.Option(None, "--commit-facts"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_ai_attribution.json")
        run_active_ai_attribution(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
        )

    @parent.command(
        "active-github-frontier",
        help="Build active-project GitHub frontier (issues/PRs) — requires gh on PATH",
    )
    def _active_github_frontier(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        work_packages: str | None = typer.Option(None, "--work-packages"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_github_frontier.json")
        run_active_github_frontier(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
            work_packages_file=work_packages or resolve_analysis_path("active_work_packages.json"),
        )

    @parent.command(
        "active-ci-health",
        help="Static .github/workflows parsing; optional gh api run telemetry",
    )
    def _active_ci_health(
        start: str = typer.Option(None, "--start"),
        end: str = typer.Option(None, "--end"),
        project: list[str] = typer.Option(None, "--project"),
        snapshot: str | None = typer.Option(None, "--snapshot"),
        include_runs: bool = typer.Option(False, "--include-runs/", help="Network: query gh api for last 30d of run history"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        target = out or resolve_analysis_path("active_ci_health.json")
        run_active_ci_health(
            target,
            start=_opt_date(start), end=_opt_date(end), projects=list(project or []),
            snapshot_file=snapshot or resolve_analysis_path("active_project_snapshot.json"),
            include_runs=include_runs,
        )


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
    except (typer.Exit, SystemExit) as exc:
        code = exc.exit_code if isinstance(exc, typer.Exit) else (exc.code or 0)
        return int(code or 0)
    return 0
