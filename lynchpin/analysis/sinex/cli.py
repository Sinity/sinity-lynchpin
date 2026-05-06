"""Sinex analysis command registration and dispatch."""

from __future__ import annotations

import argparse

from ..core.io import resolve_analysis_path, save_json


def add_commands(subparsers: argparse._SubParsersAction) -> None:
    cmd_sinex = subparsers.add_parser('sinex', help='Sinex per-crate structural analysis')
    cmd_sinex.add_argument('--repo', default='/realm/project/sinex')
    cmd_sinex.add_argument('--out', default=None)

    cmd_sinex_t = subparsers.add_parser('sinex-temporal', help='Sinex monthly velocity & crate growth')
    cmd_sinex_t.add_argument('--repo', default='/realm/project/sinex')
    cmd_sinex_t.add_argument('--out', default=None)


def run_command(args: argparse.Namespace) -> int | None:
    if args.command == 'sinex':
        from .structure import run_sinex_analysis

        out = args.out or resolve_analysis_path('sinex_structure_metrics.json')
        run_sinex_analysis(args.repo, out)
        return 0

    if args.command == 'sinex-temporal':
        from . import temporal as sinex_temporal

        out = args.out or resolve_analysis_path('sinex_temporal_metrics.json')
        monthly = sinex_temporal.compute_monthly_velocity(args.repo)
        crate_growth = sinex_temporal.compute_crate_growth(args.repo)
        stats = sinex_temporal.compute_sinex_stats(args.repo)
        save_json(out, {
            'monthly_velocity': monthly,
            'crate_growth': crate_growth,
            'stats': stats,
        })
        return 0

    return None
