"""CLI entrypoints for project analysis materializers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from ..active.git_facts import run_active_git_facts
from ..active.snapshot import run_active_project_snapshot
from ..change.work_packages import run_active_work_packages
from ..code_index.python_analysis import run_active_python_complexity, run_active_python_import_graph
from ..code_index.symbol_diffs import run_active_symbol_diffs
from ..code_index.symbol_index import run_active_symbol_index
from ..frontier.ci_health import run_active_ci_health
from ..frontier.github_frontier import run_active_github_frontier
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
    help="Project analysis materializers for velocity.",
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
        commit_facts_file=commit_facts,
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
            commit_facts_file=commit_facts,
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
        result = app(args=argv, standalone_mode=False)
        if isinstance(result, int):
            return result
    except (typer.Exit, SystemExit) as exc:
        code = exc.exit_code if isinstance(exc, typer.Exit) else (exc.code or 0)
        return int(code or 0)
    return 0
