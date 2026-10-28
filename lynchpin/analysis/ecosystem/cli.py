"""Cross-ecosystem analysis command registration and dispatch."""

from __future__ import annotations

from datetime import date

import typer

from lynchpin.core.io import resolve_analysis_path


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid date: {value!r}") from exc


def register_commands(app: typer.Typer, *, analysis_spec: str) -> None:
    @app.command("polylogue-metrics", help="Polylogue code repository analysis")
    def _polylogue_metrics(
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        from .polylogue_metrics import run_polylogue_metrics

        target = out or resolve_analysis_path("polylogue_metrics.json")
        run_polylogue_metrics(target)


    @app.command(
        "work-package-scope",
        help="Build native scope-weighted work-package model across Sinex and Polylogue",
    )
    def _work_package_scope(
        spec: str = typer.Option(analysis_spec, "--spec"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        from .work_package_scope import run_work_package_scope

        target = out or resolve_analysis_path("work_package_scope.json")
        run_work_package_scope(spec, target)

    @app.command("aw-git-join", help="Join ActivityWatch active-window data with sinex git activity")
    def _aw_git_join(
        spec: str = typer.Option(analysis_spec, "--spec"),
        out: str | None = typer.Option(None, "--out"),
        aw_db: str | None = typer.Option(None, "--aw-db"),
    ) -> None:
        from lynchpin.core.config import get_config
        from . import aw_git_join

        target = out or resolve_analysis_path("aw_git_join_metrics.json")
        aw_git_join.run_aw_git_join(
            spec_path=spec,
            out_file=target,
            aw_db_path=aw_db if aw_db is not None else str(get_config().activitywatch_db),
        )

    @app.command(
        "current-state-analysis",
        help="Materialize graph-backed active-project current-state evidence",
    )
    def _current_state_analysis(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
        out: str | None = typer.Option(None, "--out"),
        markdown_out: str | None = typer.Option(None, "--markdown-out"),
        project: list[str] = typer.Option(None, "--project"),
        github_frontier: bool = typer.Option(False, "--github-frontier/"),
        weak_tags: bool = typer.Option(False, "--weak-tags/"),
        persist_weak_tags: bool = typer.Option(False, "--persist-weak-tags/"),
    ) -> None:
        from .current_state import run_current_state_analysis

        start_d = _parse_date(start)
        end_d = _parse_date(end)
        target = out or resolve_analysis_path("current_state_context_pack.json")
        markdown_target = markdown_out or resolve_analysis_path("current_state_context_pack.md")
        run_current_state_analysis(
            start=start_d,
            end=end_d,
            out_file=target,
            markdown_out=markdown_target,
            projects=list(project or []),
            include_github_frontier=github_frontier,
            weak_tags=weak_tags,
            persist_weak_tags=persist_weak_tags,
        )
