"""Project-selection helpers shared by evidence graph builders."""

from __future__ import annotations

from collections.abc import Sequence

from ..core.projects import canonical_project_name


def selected_projects(projects: Sequence[str] | None) -> set[str]:
    if not projects:
        return set()
    return {
        project
        for project in (normalize_project(value) for value in projects)
        if project is not None
    }


def include_project(project: str | None, selected: set[str]) -> bool:
    if project is None:
        return not selected
    return not selected or project in selected


def normalize_project(value: object) -> str | None:
    return canonical_project_name(value)
