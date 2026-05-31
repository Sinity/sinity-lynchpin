"""Map materializer command registration and dispatch."""

from __future__ import annotations

import typer

from ..core import canonical as canonical_module
from lynchpin.core.io import resolve_analysis_path


def register_commands(app: typer.Typer, *, analysis_spec: str) -> None:
    @app.command("project-maps", help="Build module/hotspot maps")
    def _project_maps(
        spec: str = typer.Option(analysis_spec, "--spec"),
        module_out: str | None = typer.Option(None, "--module-out"),
        hotspot_out: str | None = typer.Option(None, "--hotspot-out"),
        markdown_out: str | None = typer.Option(None, "--markdown-out"),
    ) -> None:
        from . import project_maps as maps_project

        loaded_spec = canonical_module.load_analysis_spec(spec)
        module_target = module_out or resolve_analysis_path("module_map.json")
        hotspot_target = hotspot_out or resolve_analysis_path("hotspot_map.json")
        markdown_target = markdown_out or resolve_analysis_path("maps/project-maps.md")
        maps_project.run_project_maps(
            spec=loaded_spec,
            module_out=module_target,
            hotspot_out=hotspot_target,
            markdown_out=markdown_target,
        )

    @app.command("dependency-map", help="Build sinex workspace dependency map")
    def _dependency_map(
        spec: str = typer.Option(analysis_spec, "--spec"),
        out: str | None = typer.Option(None, "--out"),
        markdown_out: str | None = typer.Option(None, "--markdown-out"),
    ) -> None:
        from . import dependency_map as maps_dependency

        loaded_spec = canonical_module.load_analysis_spec(spec)
        out_target = out or resolve_analysis_path("dependency_map.json")
        markdown_target = markdown_out or resolve_analysis_path("maps/dependency-map.md")
        maps_dependency.run_dependency_map(
            spec=loaded_spec,
            out_file=out_target,
            markdown_out=markdown_target,
        )

    @app.command("change-surface-map", help="Build file-touch based change surface map")
    def _change_surface_map(
        spec: str = typer.Option(analysis_spec, "--spec"),
        out: str | None = typer.Option(None, "--out"),
        markdown_out: str | None = typer.Option(None, "--markdown-out"),
    ) -> None:
        from . import change_surface as maps_change_surface

        loaded_spec = canonical_module.load_analysis_spec(spec)
        out_target = out or resolve_analysis_path("change_surface_map.json")
        markdown_target = markdown_out or resolve_analysis_path("maps/change-surface-map.md")
        maps_change_surface.run_change_surface_map(
            spec=loaded_spec,
            out_file=out_target,
            markdown_out=markdown_target,
        )
