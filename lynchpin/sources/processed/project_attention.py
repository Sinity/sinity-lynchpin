"""Project attention metrics: entropy, concentration, and rotation patterns."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator


@dataclass(frozen=True)
class ProjectAttentionMetrics:
    date: date
    entropy: float
    gini: float
    top_project: str
    top_project_share: float
    project_count: int
    rotation_speed: float
    new_projects: tuple[str, ...]
    dropped_projects: tuple[str, ...]


def _gini(values: list[float]) -> float:
    if not values or sum(values) == 0:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    total = sum(sorted_v)
    cumsum = sum((i + 1) * v for i, v in enumerate(sorted_v))
    return (2 * cumsum) / (n * total) - (n + 1) / n


def _query_project_time(d: date) -> dict[str, float]:
    """Query project time for a single day from warehouse."""
    try:
        r = subprocess.run(
            [
                "duckdb",
                "artefacts/lynchpin/warehouse.duckdb",
                "-c",
                f"SELECT project, duration_seconds FROM trajectory_day_project WHERE date='{d}' AND duration_seconds > 300",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        result: dict[str, float] = {}
        for line in r.stdout.strip().split("\n"):
            parts = line.strip().split("│")
            if len(parts) >= 3:
                try:
                    proj = parts[1].strip()
                    secs = float(parts[2].strip())
                    if proj:
                        result[proj] = secs
                except (ValueError, IndexError):
                    pass
        return result
    except Exception:
        return {}


def _query_active_hours(d: date) -> float:
    try:
        r = subprocess.run(
            [
                "duckdb",
                "artefacts/lynchpin/warehouse.duckdb",
                "-c",
                f"SELECT active_seconds/3600.0 FROM trajectory_day WHERE date='{d}'",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.strip().split("\n"):
            parts = line.strip().split("│")
            if len(parts) >= 2:
                try:
                    return float(parts[1].strip())
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass
    return 0.0


def iter_project_attention(
    *, start: date, end: date
) -> Iterator[ProjectAttentionMetrics]:
    # Collect prior 7 days' projects for novelty detection
    prior_projects: dict[date, set[str]] = {}
    for i in range(7):
        prior_d = start - timedelta(days=i + 1)
        pt = _query_project_time(prior_d)
        prior_projects[prior_d] = set(pt.keys())

    prior_union = (
        set().union(*prior_projects.values()) if prior_projects else set()
    )
    prior_intersection = (
        set.intersection(*prior_projects.values())
        if prior_projects and all(prior_projects.values())
        else set()
    )

    d = start
    while d <= end:
        pt = _query_project_time(d)
        if not pt:
            d += timedelta(days=1)
            continue

        total_secs = sum(pt.values())
        fractions = [v / total_secs for v in pt.values()]

        # Shannon entropy
        entropy = -sum(p * math.log2(p) for p in fractions if p > 0)
        gini = _gini(list(pt.values()))

        sorted_projects = sorted(pt.items(), key=lambda x: -x[1])
        top_proj = sorted_projects[0][0]
        top_share = sorted_projects[0][1] / total_secs

        active_h = _query_active_hours(d)
        today_projects = set(pt.keys())
        new = tuple(sorted(today_projects - prior_union))
        dropped = tuple(sorted(prior_intersection - today_projects))

        yield ProjectAttentionMetrics(
            date=d,
            entropy=entropy,
            gini=gini,
            top_project=top_proj,
            top_project_share=top_share,
            project_count=len(pt),
            rotation_speed=len(pt) / max(active_h, 0.1),
            new_projects=new,
            dropped_projects=dropped,
        )

        # Update prior window
        prior_projects[d] = today_projects
        oldest = min(prior_projects.keys())
        if len(prior_projects) > 7:
            del prior_projects[oldest]
        prior_union = set().union(*prior_projects.values())
        prior_intersection = (
            set.intersection(
                *[v for v in prior_projects.values() if v]
            )
            if prior_projects
            else set()
        )

        d += timedelta(days=1)
