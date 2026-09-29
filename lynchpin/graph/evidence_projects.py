"""Project-selection helpers shared by evidence graph builders."""

from __future__ import annotations

from collections.abc import Sequence

from ..core.projects import canonical_project_name, resolve_project_selection


def selected_projects(projects: Sequence[str] | None) -> set[str]:
    """Return the builder filter set; empty means every project.

    Graph builders treat an omitted or empty selection as unrestricted. A
    named project that resolves to nothing raises ``UnknownProjectError``
    rather than silently widening the selection to every project.
    """
    return set(resolve_project_selection(projects) or ())


def include_project(project: str | None, selected: set[str]) -> bool:
    if project is None:
        return not selected
    return not selected or project in selected


def normalize_project(value: object) -> str | None:
    return canonical_project_name(value)
