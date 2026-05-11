"""AI-attribution longitudinal backfill (M.13).

Where ``active.ai_attribution`` answers "for the last 31 days, which commits
co-occurred with AI sessions?", this module answers "across all of history,
how has AI co-authorship trended?"

Aggregates the same per-commit attribution at monthly granularity so the
trend is visible: when did AI assistance become significant per project?
which projects show high AI ratios? which months were AI-light?

Implementation reuses ``ai_attribution._classify_commit`` for per-commit
labels (so the longitudinal series stays consistent with the 31-day
artifact's methodology), but iterates chunk-by-chunk over the full
``active_commit_facts.json`` history.

Output schema:

    {
      "generated_at_utc": "...",
      "window": {"start": "2023-01", "end": "2026-05"},
      "monthly": [
        {
          "month": "2026-05",
          "project": "lynchpin",
          "total_commits": 42,
          "attributed_high": 12,
          "attributed_medium": 25,
          "attributed_none": 5,
          "ai_ratio": 0.881,
          "dominant_kinds": {"implementation": 18, "debugging": 8}
        },
        ...
      ],
      "project_totals": [...],
      "caveats": [...]
    }
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from os import PathLike
from typing import Any, Iterable, Sequence

from ...sources.polylogue import SessionProfile, iter_session_profiles
from ..active.ai_attribution import _classify_commit, _index_sessions, _parse_date, _parse_dt
from ...substrate.reader import read_commit_facts
from ...substrate.connection import connect, substrate_path
from ..core.io import resolve_analysis_path, save_json


def build_active_ai_attribution_history(
    *,
    projects: Sequence[str] | None = None,
    commit_payload: dict[str, Any] | None = None,
    session_profiles: Iterable[SessionProfile] | None = None,
) -> dict[str, Any]:
    """Build the longitudinal series. Reads from the substrate commit_fact
    table (which covers all available history) and the full polylogue
    archive. No date bounds because the point is the trend across all of
    it."""

    if commit_payload is None:
        with connect(substrate_path()) as conn:
            commit_payload = read_commit_facts(conn)
    selected = set(projects or ())

    # Index sessions across ALL of polylogue history, not the last 31 days.
    profiles = (
        list(session_profiles) if session_profiles is not None else list(iter_session_profiles())
    )
    if profiles:
        # Find the actual span of available session data.
        session_dates = [
            p.canonical_session_date for p in profiles
            if p.canonical_session_date is not None
        ]
        if session_dates:
            session_start = min(session_dates)
            session_end = max(session_dates)
        else:
            session_start = date(2000, 1, 1)
            session_end = date.today()
    else:
        session_start = date(2000, 1, 1)
        session_end = date.today()

    sessions_by_project_day, session_windows = _index_sessions(
        profiles, start=session_start, end=session_end, selected=selected,
    )

    # Per (month, project) rollup.
    monthly: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "total_commits": 0,
            "attributed_high": 0,
            "attributed_medium": 0,
            "attributed_none": 0,
            "kind_counts": Counter(),
        }
    )
    overall_dates: list[date] = []
    project_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_commits": 0, "high": 0, "medium": 0, "none": 0}
    )

    profile_kind_by_id = {p.conversation_id: p.work_event_kind for p in profiles if p.work_event_kind}

    for commit in commit_payload.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        project = commit.get("project")
        if not project or (selected and project not in selected):
            continue
        commit_ts = _parse_dt(commit.get("timestamp"))
        commit_day = _parse_date(commit.get("date"))
        if commit_day is None:
            continue
        attribution, supporting = _classify_commit(
            project=project,
            commit_ts=commit_ts,
            commit_day=commit_day,
            session_windows=session_windows.get(project, ()),
            same_day_index=sessions_by_project_day,
        )
        month_key = f"{commit_day.year:04d}-{commit_day.month:02d}"
        bucket = monthly[(month_key, project)]
        bucket["total_commits"] += 1
        if attribution == "high":
            bucket["attributed_high"] += 1
            project_totals[project]["high"] += 1
        elif attribution == "medium":
            bucket["attributed_medium"] += 1
            project_totals[project]["medium"] += 1
        else:
            bucket["attributed_none"] += 1
            project_totals[project]["none"] += 1
        project_totals[project]["total_commits"] += 1
        overall_dates.append(commit_day)
        # Attribute kind for the supporting sessions where work_event_kind is known.
        for session_id, _provider in supporting:
            kind = profile_kind_by_id.get(session_id)
            if kind:
                bucket["kind_counts"][kind] += 1

    monthly_rows: list[dict[str, Any]] = []
    for (month, project), data in sorted(monthly.items()):
        total = data["total_commits"]
        attributed = data["attributed_high"] + data["attributed_medium"]
        ratio = attributed / total if total else 0.0
        monthly_rows.append({
            "month": month,
            "project": project,
            "total_commits": total,
            "attributed_high": data["attributed_high"],
            "attributed_medium": data["attributed_medium"],
            "attributed_none": data["attributed_none"],
            "ai_ratio": round(ratio, 3),
            "dominant_kinds": dict(data["kind_counts"].most_common(5)),
        })

    project_total_rows: list[dict[str, Any]] = []
    for project in sorted(project_totals):
        data = project_totals[project]
        total = data["total_commits"]
        attributed = data["high"] + data["medium"]
        ratio = attributed / total if total else 0.0
        project_total_rows.append({
            "project": project,
            "total_commits": total,
            "high": data["high"],
            "medium": data["medium"],
            "none": data["none"],
            "ai_ratio": round(ratio, 3),
        })

    if overall_dates:
        window_start = f"{min(overall_dates).year:04d}-{min(overall_dates).month:02d}"
        window_end = f"{max(overall_dates).year:04d}-{max(overall_dates).month:02d}"
    else:
        window_start = window_end = ""

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": window_start, "end": window_end},
        "monthly": monthly_rows,
        "project_totals": project_total_rows,
        "session_window_count": sum(len(v) for v in session_windows.values()),
        "session_count": len(profiles),
        "caveats": [
            "high = polylogue session window contained the commit timestamp",
            "medium = polylogue session landed same calendar day for the same project, but no precise window-overlap",
            "none = neither — Co-Authored-By trailers (independent) may still raise the baseline elsewhere",
            "older months may show fewer attributions if polylogue archive coverage is thinner there",
            "dominant_kinds reflects work_event_kind on supporting sessions when populated; older sessions may lack kind labels",
        ],
    }


def run_active_ai_attribution_history(
    out_file: str | PathLike[str],
    *,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_ai_attribution_history(projects=projects)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


__all__ = [
    "build_active_ai_attribution_history",
    "run_active_ai_attribution_history",
]
