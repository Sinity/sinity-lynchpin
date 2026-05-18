"""Per-commit AI-assistance density (Arc L.2).

Where ``active_ai_attribution`` answers "did an AI session co-occur with this
commit?" at the session level, this module answers a tighter question:

> Of the AI work-events whose time window overlaps the commit's authoring
> window AND whose ``file_paths`` intersect the commit's paths, how dense
> was the help?

Density buckets (per the maximally-ambitious plan, Arc L.2):

- ``high``    — ≥3 file-overlapping events with ≥2 hours total event time
- ``medium``  — 1–2 file-overlapping events, OR cumulative duration 30min–2h
- ``low``     — reserved for future explicit low-confidence file-overlap
                signals; same-day-only evidence is not assistance density
- ``none``    — no AI sessions for this project on the commit's logical day

Output ``active_ai_assist_density.json`` is a sibling of
``active_ai_attribution.json``; it does not replace it. Density rides on
heuristic kind labels (Arc K), so each row carries a kind-tier caveat when
the supporting events were dominated by low-tier kinds.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from typing import Any, Iterable, Sequence

from ...core.parse import parse_datetime as _parse_dt
from ...core.primitives import logical_date
from ...core.projects import canonical_project_name
from ...sources.polylogue import WorkEvent, work_events
from ...substrate.work_commits import read_commit_facts
from ...substrate.connection import connect, substrate_path
from ..core.io import resolve_analysis_path, save_json


_HIGH_EVENT_THRESHOLD = 3
_HIGH_DURATION_S = 2 * 3600
_MEDIUM_DURATION_S_MIN = 30 * 60
_MEDIUM_DURATION_S_MAX = 2 * 3600


def build_active_ai_assist_density(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    commit_payload: dict[str, Any] | None = None,
    work_events_iter: Iterable[WorkEvent] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    if commit_payload is None:
        with connect(substrate_path()) as conn:
            commit_payload = read_commit_facts(
                conn,
                start=start,
                end=end,
                projects=tuple(projects) if projects else None,
            )
    selected = {canonical_project_name(p) or p for p in (projects or ())}

    events = (
        tuple(work_events_iter)
        if work_events_iter is not None
        else tuple(work_events(start=start, end=end + timedelta(days=1)))
    )
    _by_project_day, by_event_project = _index_events(events, selected=selected)

    rows: list[dict[str, Any]] = []
    project_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    overall: Counter[str] = Counter()

    for commit in commit_payload.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        project = commit.get("project")
        if not project or (selected and project not in selected):
            continue
        commit_ts = _parse_dt(commit.get("timestamp"))
        commit_day = _parse_date(commit.get("date"))
        commit_paths = {str(p) for p in (commit.get("paths") or []) if p}

        density, supporting, total_duration_s, low_tier_count = _classify(
            project=project,
            commit_ts=commit_ts,
            commit_day=commit_day,
            commit_paths=commit_paths,
            project_events=by_event_project.get(project, ()),
        )
        project_buckets[project][density] += 1
        overall[density] += 1

        rows.append(
            {
                "project": project,
                "sha": commit.get("sha"),
                "subject": commit.get("subject"),
                "timestamp": commit.get("timestamp"),
                "date": commit.get("date"),
                "ai_assist_density": density,
                "supporting_event_count": len(supporting),
                "supporting_total_duration_s": total_duration_s,
                "supporting_event_ids": [event_id for event_id, _, _ in supporting],
                "supporting_kinds": _kind_summary(supporting),
                "low_tier_event_count": low_tier_count,
                "caveats": _row_caveats(density, supporting, low_tier_count),
            }
        )

    project_summary = [
        {
            "project": project,
            "high": counts.get("high", 0),
            "medium": counts.get("medium", 0),
            "low": counts.get("low", 0),
            "none": counts.get("none", 0),
            "total": sum(counts.values()),
        }
        for project, counts in sorted(project_buckets.items())
    ]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "projects": project_summary,
        "summary": {
            "total_commits": sum(overall.values()),
            "high": overall.get("high", 0),
            "medium": overall.get("medium", 0),
            "low": overall.get("low", 0),
            "none": overall.get("none", 0),
            "event_count_in_window": len(events),
        },
        "commits": rows,
        "caveats": [
            "density bucketing is a deterministic heuristic over Polylogue's heuristic work-event boundaries and kind labels — see Arc K caveats",
            "file_paths overlap is co-occurrence evidence, not authorship proof",
            "Co-Authored-By trailer detection (lynchpin.sources.git) is independent and may raise the AI-assistance baseline elsewhere",
        ],
    }


def run_active_ai_assist_density(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_ai_assist_density(start=start, end=end, projects=projects)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _classify(
    *,
    project: str,
    commit_ts: datetime | None,
    commit_day: date | None,
    commit_paths: set[str],
    project_events: Sequence[WorkEvent],
) -> tuple[str, list[tuple[str, str, str]], int, int]:
    """Return (density, supporting_events, total_duration_s, low_tier_count)."""
    supporting: list[tuple[str, str, str]] = []  # (event_id, kind, provider)
    total_duration_s = 0
    low_tier_count = 0

    if commit_ts is None or commit_day is None:
        return "none", supporting, total_duration_s, low_tier_count

    # Window-overlap + file-overlap → "supporting" events.
    for event in project_events:
        if event.start is None or event.end is None:
            continue
        if not (event.start <= commit_ts <= event.end):
            continue
        if commit_paths and not (set(event.file_paths) & commit_paths):
            continue
        supporting.append((event.event_id, event.kind, event.provider))
        if event.duration_ms:
            total_duration_s += event.duration_ms // 1000
        # Polylogue confidence < 0.5 is a low-tier signal at the source level
        # (Arc K uses 0.5/0.8 thresholds; the overlay's conf isn't on the raw
        # event, only on the graph node — so we approximate from event.confidence).
        if (event.confidence or 0.0) < 0.5:
            low_tier_count += 1

    if supporting:
        if (
            len(supporting) >= _HIGH_EVENT_THRESHOLD
            and total_duration_s >= _HIGH_DURATION_S
        ):
            return "high", supporting, total_duration_s, low_tier_count
        if (
            1 <= len(supporting) <= 2
            or _MEDIUM_DURATION_S_MIN <= total_duration_s <= _MEDIUM_DURATION_S_MAX
        ):
            return "medium", supporting, total_duration_s, low_tier_count
        # ≥3 events but under duration threshold → still substantive: medium.
        return "medium", supporting, total_duration_s, low_tier_count

    return "none", supporting, total_duration_s, low_tier_count


def _index_events(
    events: Iterable[WorkEvent],
    *,
    selected: set[str],
) -> tuple[
    dict[tuple[str, date], list[WorkEvent]],
    dict[str, list[WorkEvent]],
]:
    """Bucket events by (project, day) and by project from file paths only."""
    by_project_day: dict[tuple[str, date], list[WorkEvent]] = defaultdict(list)
    by_project: dict[str, list[WorkEvent]] = defaultdict(list)
    for event in events:
        if event.start is None:
            continue
        day = logical_date(event.start)
        projects = _projects_for_event(event)
        for project in projects:
            if selected and project not in selected:
                continue
            by_project[project].append(event)
            by_project_day[(project, day)].append(event)
    return by_project_day, by_project


def _projects_for_event(event: WorkEvent) -> list[str]:
    seen: list[str] = []
    for path in event.file_paths:
        project = canonical_project_name(path)
        if project and project not in seen:
            seen.append(project)
    return seen or []


def _kind_summary(supporting: Sequence[tuple[str, str, str]]) -> dict[str, int]:
    counter: Counter[str] = Counter(kind for _, kind, _ in supporting)
    return dict(counter.most_common())


def _row_caveats(
    density: str,
    supporting: Sequence[tuple[str, str, str]],
    low_tier_count: int,
) -> list[str]:
    caveats: list[str] = []
    if low_tier_count and supporting:
        ratio = low_tier_count / len(supporting)
        if ratio >= 0.5:
            caveats.append(
                f"{low_tier_count}/{len(supporting)} supporting events have low Polylogue kind confidence — see Arc K"
            )
    return caveats




def _parse_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "build_active_ai_assist_density",
    "run_active_ai_assist_density",
]
