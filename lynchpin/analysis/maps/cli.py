"""Map materializer command registration and dispatch."""

from __future__ import annotations

import argparse

from ..core import canonical as canonical_module
from ..core.io import resolve_analysis_path


def add_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], *, analysis_spec: str) -> None:
    cmd_maps = subparsers.add_parser('project-maps', help='Build module/hotspot maps')
    cmd_maps.add_argument('--spec', default=analysis_spec)
    cmd_maps.add_argument('--module-out', default=None)
    cmd_maps.add_argument('--hotspot-out', default=None)
    cmd_maps.add_argument('--markdown-out', default=None)

    cmd_dep_map = subparsers.add_parser('dependency-map', help='Build sinex workspace dependency map')
    cmd_dep_map.add_argument('--spec', default=analysis_spec)
    cmd_dep_map.add_argument('--out', default=None)
    cmd_dep_map.add_argument('--markdown-out', default=None)

    cmd_change_surface = subparsers.add_parser(
        'change-surface-map',
        help='Build file-touch based change surface map',
    )
    cmd_change_surface.add_argument('--spec', default=analysis_spec)
    cmd_change_surface.add_argument('--out', default=None)
    cmd_change_surface.add_argument('--markdown-out', default=None)


def run_command(args: argparse.Namespace) -> int | None:
    if args.command == 'project-maps':
        from . import project_maps as maps_project

        spec = canonical_module.load_analysis_spec(args.spec)
        module_out = args.module_out or resolve_analysis_path('module_map.json')
        hotspot_out = args.hotspot_out or resolve_analysis_path('hotspot_map.json')
        markdown_out = args.markdown_out or resolve_analysis_path('maps/project-maps.md')
        maps_project.run_project_maps(
            spec=spec,
            module_out=module_out,
            hotspot_out=hotspot_out,
            markdown_out=markdown_out,
        )
        return 0

    if args.command == 'dependency-map':
        from . import dependency_map as maps_dependency

        spec = canonical_module.load_analysis_spec(args.spec)
        out = args.out or resolve_analysis_path('dependency_map.json')
        markdown_out = args.markdown_out or resolve_analysis_path('maps/dependency-map.md')
        maps_dependency.run_dependency_map(
            spec=spec,
            out_file=out,
            markdown_out=markdown_out,
        )
        return 0

    if args.command == 'change-surface-map':
        from . import change_surface as maps_change_surface

        spec = canonical_module.load_analysis_spec(args.spec)
        out = args.out or resolve_analysis_path('change_surface_map.json')
        markdown_out = args.markdown_out or resolve_analysis_path('maps/change-surface-map.md')
        maps_change_surface.run_change_surface_map(
            spec=spec,
            out_file=out,
            markdown_out=markdown_out,
        )
        return 0

    return None
