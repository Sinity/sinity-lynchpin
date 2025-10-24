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
    @app.command("polylogue-metrics", help="Live polylogue repo + archive analysis")
    def _polylogue_metrics(
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        from .polylogue_metrics import run_polylogue_metrics

        target = out or resolve_analysis_path("polylogue_metrics.json")
        run_polylogue_metrics(target)

    @app.command("polylogue-archive-shape", help="Raw Polylogue/Codex/Claude archive coverage and render-size ratios")
    def _polylogue_archive_shape(
        out: str | None = typer.Option(None, "--out"),
        markdown_out: str | None = typer.Option(None, "--markdown-out"),
        sample_per_provider: int | None = typer.Option(
            100,
            "--sample-per-provider",
            help="Stratified raw files to render per provider; use --all to measure every nonempty file.",
        ),
        measure_all: bool = typer.Option(False, "--all/--sample", help="Measure every nonempty raw log file."),
    ) -> None:
        from .polylogue_archive_shape import run_polylogue_archive_shape

        target = out or resolve_analysis_path("polylogue_archive_shape.json")
        markdown_target = markdown_out or resolve_analysis_path("polylogue_archive_shape.md")
        run_polylogue_archive_shape(
            target,
            markdown_out=markdown_target,
            sample_per_provider=None if measure_all else sample_per_provider,
        )

    @app.command("polylogue-time-composition", help="Polylogue session timeline and time-composition analysis")
    def _polylogue_time_composition(
        start: str = typer.Option(..., "--start"),
        end: str = typer.Option(..., "--end"),
        out: str | None = typer.Option(None, "--out"),
        markdown_out: str | None = typer.Option(None, "--markdown-out"),
        session_id: str | None = typer.Option(None, "--session-id"),
        limit: int | None = typer.Option(50, "--limit"),
        cross_source: bool = typer.Option(True, "--cross-source/--no-cross-source"),
    ) -> None:
        from .polylogue_time_composition import run_polylogue_time_composition

        target = out or resolve_analysis_path("polylogue_time_composition.json")
        markdown_target = markdown_out or resolve_analysis_path("polylogue_time_composition.md")
        run_polylogue_time_composition(
            target,
            start=_parse_date(start),
            end=_parse_date(end),
            markdown_out=markdown_target,
            session_id=session_id,
            limit=limit,
            include_cross_source=cross_source,
        )

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
        aw_db: str = typer.Option(
            "/realm/home/.local/share/activitywatch/aw-server-rust/sqlite.db",
            "--aw-db",
        ),
    ) -> None:
        from . import aw_git_join

        target = out or resolve_analysis_path("aw_git_join_metrics.json")
        aw_git_join.run_aw_git_join(
            spec_path=spec,
            out_file=target,
            aw_db_path=aw_db,
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
