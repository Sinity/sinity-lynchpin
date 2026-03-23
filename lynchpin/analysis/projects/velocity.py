"""Project-level git velocity analysis and dashboard rendering.

This module re-exports the public API from velocity_analysis and
velocity_renderer so existing callers continue to work unchanged.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Dict

from ...core.projects import ProjectProfile
from .velocity_analysis import (
    AGGREGATE_PROJECT,
    AGGREGATE_PALETTE,
    SKIP_EXTENSIONS,
    SKIP_PATHS,
    AuthorStats,
    CategoryStats,
    CommitEvent,
    DailyStats,
    LogFn,
    ProjectStats,
    PROJECT_SPECS,
    _aggregate_spec,
    _aggregate_stats,
    _collapse_commit,
    _noop,
    _skip_common,
    analyze_projects,
    module_from_path,
    parse_log,
    run_git_log,
    run_git_tags,
)
from .velocity_renderer import (
    DEFAULT_OUTPUT,
    render_velocity_dashboard,
)


def select_project_profiles(
    *,
    project_names: Sequence[str] | None = None,
    exclude_names: Sequence[str] | None = None,
) -> Dict[str, ProjectProfile]:
    selected_specs: Dict[str, ProjectProfile] = dict(PROJECT_SPECS)
    if project_names is not None:
        requested = [name.strip() for name in project_names if name.strip()]
        if not requested:
            raise ValueError("At least one non-empty project name is required.")
        missing = [name for name in requested if name not in PROJECT_SPECS]
        if missing:
            raise ValueError(f"Unknown project(s): {', '.join(sorted(missing))}")
        selected_specs = {name: PROJECT_SPECS[name] for name in requested}

    if exclude_names is not None:
        excluded = [name.strip() for name in exclude_names if name.strip()]
        if not excluded:
            raise ValueError("At least one non-empty excluded project name is required.")
        missing = [name for name in excluded if name not in PROJECT_SPECS]
        if missing:
            raise ValueError(f"Unknown project(s): {', '.join(sorted(missing))}")
        for name in excluded:
            selected_specs.pop(name, None)

    if not selected_specs:
        raise ValueError("No projects available to analyse.")
    return selected_specs


def build_velocity_dashboard(
    *,
    output: Path = DEFAULT_OUTPUT,
    project_names: Sequence[str] | None = None,
    exclude_names: Sequence[str] | None = None,
    aggregate: bool = True,
    log: LogFn | None = None,
) -> bool:
    if log is None:
        log = _noop
    selected_specs = select_project_profiles(
        project_names=project_names,
        exclude_names=exclude_names,
    )
    stats = analyze_projects(selected_specs, log=log)
    if not stats:
        log("No repositories produced git history; nothing to render.")
        return False
    if aggregate and len(stats) > 1:
        aggregate_stats = _aggregate_stats(stats)
        aggregate_spec = _aggregate_spec(list(stats.keys()))
        stats = {AGGREGATE_PROJECT: aggregate_stats, **stats}
        selected_specs = {AGGREGATE_PROJECT: aggregate_spec, **selected_specs}
    return render_velocity_dashboard(stats, selected_specs, output, log=log)
