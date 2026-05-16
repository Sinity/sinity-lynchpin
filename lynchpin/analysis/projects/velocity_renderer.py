"""HTML/ECharts rendering for the cross-repo velocity dashboard.

The dashboard is generated into the configured knowledgebase-backed artefact
root. The categorization comes from the project profiles in
`lynchpin.core.projects`.
"""

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Dict

from ...core.projects import ProjectProfile
from ...core.cache import write_text_if_changed
from ...core.config import get_config
from .velocity_analysis import (
    AGGREGATE_PROJECT,
    LogFn,
    ProjectStats,
    _aggregate_spec,
    _aggregate_stats,
    _noop,
    analyze_projects,
    select_project_profiles,
)
from .velocity_payload import build_velocity_dashboard_payload
from .velocity_template import HTML_TEMPLATE

DEFAULT_OUTPUT = get_config().velocity_output


def build_velocity_dashboard(
    *,
    output: Path = DEFAULT_OUTPUT,
    project_names: list[str] | None = None,
    exclude_names: list[str] | None = None,
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


def render_velocity_dashboard(
    all_stats: Dict[str, ProjectStats],
    project_specs: Mapping[str, ProjectProfile],
    output_path: Path,
    *,
    log: LogFn | None = None,
) -> bool:
    if log is None:
        log = _noop
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_payload = build_velocity_dashboard_payload(all_stats, project_specs)
    if dashboard_payload is None:
        log("No data found.")
        return False

    html = HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(dashboard_payload)).replace(
        "__GENERATED_AT__", dashboard_payload["generatedAt"]
    )

    wrote = write_text_if_changed(output_path, html)
    if wrote:
        log(f"Rich report generated at {output_path.resolve()}")
    else:
        log(f"Velocity report unchanged at {output_path.resolve()}")
    return wrote
