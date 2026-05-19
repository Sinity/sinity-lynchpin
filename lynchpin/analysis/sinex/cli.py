"""Sinex analysis command registration and dispatch."""

from __future__ import annotations

import typer

from ..core.io import resolve_analysis_path, save_json


def register_commands(app: typer.Typer) -> None:
    @app.command("sinex", help="Sinex per-crate structural analysis")
    def _sinex(
        repo: str = typer.Option("/realm/project/sinex", "--repo"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        from .structure import run_sinex_analysis

        target = out or resolve_analysis_path("sinex_structure_metrics.json")
        run_sinex_analysis(repo, target)

    @app.command("sinex-temporal", help="Sinex monthly velocity & crate growth")
    def _sinex_temporal(
        repo: str = typer.Option("/realm/project/sinex", "--repo"),
        out: str | None = typer.Option(None, "--out"),
    ) -> None:
        from . import temporal as sinex_temporal

        target = out or resolve_analysis_path("sinex_temporal_metrics.json")
        monthly = sinex_temporal.compute_monthly_velocity(repo)
        crate_growth = sinex_temporal.compute_crate_growth(repo)
        stats = sinex_temporal.compute_sinex_stats(repo)
        save_json(target, {
            "monthly_velocity": monthly,
            "crate_growth": crate_growth,
            "stats": stats,
        })
