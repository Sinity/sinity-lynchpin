"""AI co-authorship backfill via polylogue session join.

Joins commit facts from the DuckDB substrate against
``polylogue.iter_session_profiles()`` to attribute each commit at one of three
confidence bands:

- ``high``  — a polylogue session for the same canonical project window-overlaps
  the commit timestamp (session_start <= commit_ts <= session_end).
- ``medium`` — a polylogue session for the same project landed on the same
  calendar day as the commit, but no precise window-overlap.
- ``none`` — neither, so we record the commit as not visibly AI-assisted from
  the polylogue side. Co-Authored-By trailer detection (in
  ``lynchpin.sources.git``) is an independent signal and may still raise the
  baseline elsewhere.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from typing import Any, Iterable, Sequence

from ...core.parse import parse_datetime as _parse_dt
from ...core.projects import canonical_project_name
from ...sources.polylogue import SessionProfile, iter_session_profiles
from ...substrate.work_commits import read_commit_facts
from ...substrate.connection import connect, substrate_path
from lynchpin.core.io import resolve_analysis_path, save_json


def build_active_ai_attribution(
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
    session_profiles: Iterable[SessionProfile] | None = None,
) -> dict[str, Any]:
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=31))

    with connect(substrate_path()) as conn:
        commit_payload = read_commit_facts(
            conn,
            start=start,
            end=end,
            projects=tuple(projects) if projects else None,
        )
    selected = set(projects or ())

    # Graceful-degrade: when polylogue is rematerializing or session_insights
    # are incomplete, iter_session_profiles() raises. Treat that as
    # "no AI attribution available for this window" rather than crashing
    # the whole DAG step — git facts can still be reported as unattributed.
    from ...sources.polylogue import PolylogueMaterializationError
    try:
        profile_iter = (
            session_profiles if session_profiles is not None else iter_session_profiles()
        )
        sessions_by_project_day, session_windows = _index_sessions(
            profile_iter,
            start=start,
            end=end,
            selected=selected,
        )
    except PolylogueMaterializationError:
        sessions_by_project_day, session_windows = {}, {}

    rows: list[dict[str, Any]] = []
    project_counters: dict[str, Counter[str]] = defaultdict(Counter)
    project_providers: dict[str, Counter[str]] = defaultdict(Counter)
    overall: Counter[str] = Counter()
    overall_providers: Counter[str] = Counter()
    for commit in commit_payload.get("commits") or []:
        if not isinstance(commit, dict):
            continue
        project = commit.get("project")
        if not project or (selected and project not in selected):
            continue
        sha = commit.get("sha") or ""
        subject = commit.get("subject") or ""
        commit_ts = _parse_dt(commit.get("timestamp"))
        commit_day = _parse_date(commit.get("date"))
        attribution, supporting = _classify_commit(
            project=project,
            commit_ts=commit_ts,
            commit_day=commit_day,
            session_windows=session_windows.get(project, ()),
            same_day_index=sessions_by_project_day,
        )
        project_counters[project][attribution] += 1
        overall[attribution] += 1
        supporting_providers = sorted({p for _, p in supporting})
        if attribution != "none":
            for prov in supporting_providers:
                project_providers[project][prov] += 1
                overall_providers[prov] += 1
        rows.append(
            {
                "project": project,
                "sha": sha,
                "short_sha": commit.get("short_sha"),
                "date": commit.get("date"),
                "timestamp": commit.get("timestamp"),
                "subject": subject[:160],
                "ai_attribution": attribution,
                "supporting_session_ids": [sid for sid, _ in supporting[:8]],
                "supporting_providers": supporting_providers,
                "supporting_session_count": len(supporting),
            }
        )

    project_summary = []
    for project in sorted(project_counters):
        counts = project_counters[project]
        total = sum(counts.values()) or 1
        project_summary.append(
            {
                "project": project,
                "commit_count": sum(counts.values()),
                "high": counts.get("high", 0),
                "medium": counts.get("medium", 0),
                "none": counts.get("none", 0),
                "ai_assisted_ratio": round(
                    (counts.get("high", 0) + counts.get("medium", 0)) / total, 3
                ),
                "providers": dict(project_providers[project].most_common()),
            }
        )

    caveats: list[str] = []
    if not session_windows:
        caveats.append(
            "no polylogue session profiles in window; commits are marked 'none' only for this join surface"
        )
    if not commit_payload.get("commits"):
        caveats.append("commit_fact substrate table is empty for the selected window")
    caveats.append(
        "Co-Authored-By trailer detection is not consulted here — those commits would still appear "
        "as 'none' from the polylogue side; combine with sources.git ai_coauthored for fuller coverage"
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "methodology": {
            "join": "commit_fact × polylogue.iter_session_profiles on (canonical project, timestamp window)",
            "high": "session.first_message_at <= commit.timestamp <= session.last_message_at",
            "medium": "any session for the same project landed on the same calendar day",
            "none": "no overlapping session — does not exclude Co-Authored-By trailer evidence",
        },
        "projects": project_summary,
        "summary": {
            "total_commits": sum(overall.values()),
            "high": overall.get("high", 0),
            "medium": overall.get("medium", 0),
            "none": overall.get("none", 0),
            "providers": dict(overall_providers.most_common()),
            "session_window_count": sum(len(v) for v in session_windows.values()),
        },
        "commits": rows,
        "caveats": caveats,
    }


def run_active_ai_attribution(
    out_file: str | PathLike[str],
    *,
    start: date | None = None,
    end: date | None = None,
    projects: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload = build_active_ai_attribution(
        start=start,
        end=end,
        projects=projects,
    )
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


def _classify_commit(
    *,
    project: str,
    commit_ts: datetime | None,
    commit_day: date | None,
    session_windows: Sequence[tuple[datetime, datetime, str, str]],
    same_day_index: dict[tuple[str, date], list[tuple[str, str]]],
) -> tuple[str, list[tuple[str, str]]]:
    high_supporting: list[tuple[str, str]] = []
    if commit_ts is not None:
        for session_start, session_end, session_id, provider in session_windows:
            if session_start <= commit_ts <= session_end:
                high_supporting.append((session_id, provider))
    if high_supporting:
        return "high", high_supporting
    if commit_day is not None:
        same_day = same_day_index.get((project, commit_day), [])
        if same_day:
            return "medium", list(same_day)
    return "none", []


def _index_sessions(
    profiles: Iterable[SessionProfile],
    *,
    start: date,
    end: date,
    selected: set[str],
) -> tuple[
    dict[tuple[str, date], list[tuple[str, str]]],
    dict[str, list[tuple[datetime, datetime, str, str]]],
]:
    same_day: dict[tuple[str, date], list[tuple[str, str]]] = defaultdict(list)
    windows: dict[str, list[tuple[datetime, datetime, str, str]]] = defaultdict(list)
    for profile in profiles:
        session_start = profile.first_message_at
        session_end = profile.last_message_at or session_start
        session_day = profile.canonical_session_date
        if session_day is None and session_start is not None:
            session_day = session_start.date()
        if session_day is None:
            continue
        if session_day < start or session_day > end:
            continue
        provider = profile.provider or "unknown"
        for raw_project in profile.work_event_projects:
            project = canonical_project_name(raw_project)
            if project is None:
                continue
            if selected and project not in selected:
                continue
            same_day[(project, session_day)].append((profile.conversation_id, provider))
            if session_start is not None and session_end is not None:
                windows[project].append(
                    (session_start, session_end, profile.conversation_id, provider)
                )
    return same_day, windows




def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["build_active_ai_attribution", "run_active_ai_attribution"]
