"""AI-attribution backfill for substrate promotion."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

_MAX_COMMIT_PATHS = 128
_MAX_EVENT_PATHS = 128


def _backfill_ai_attribution(
    conn: Any,
    *,
    refresh_id: str,
    time_window_hours: int = 24,
) -> int:
    """Populate commit_fact.ai_attribution from project/path/timestamp overlap."""
    commit_rows = conn.execute(
        """
        SELECT
            sha, repo, project, authored_at, paths
        FROM commit_fact
        WHERE refresh_id = ?
          AND project IS NOT NULL
          AND authored_at IS NOT NULL
          AND len(paths) > 0
        """,
        [refresh_id],
    ).fetchall()
    event_rows = conn.execute(
        """
        SELECT
            event_id, project, kind, start_ts, file_paths
        FROM ai_work_event
        WHERE refresh_id = ?
          AND project IS NOT NULL
          AND start_ts IS NOT NULL
          AND len(file_paths) > 0
        """,
        [refresh_id],
    ).fetchall()

    events_by_project: dict[str, list[tuple[str, str, datetime, tuple[str, ...]]]] = {}
    for event_id, project, kind, start_ts, file_paths in event_rows:
        event_paths = _bounded_normalized_paths(file_paths, _MAX_EVENT_PATHS)
        if not event_paths:
            continue
        events_by_project.setdefault(str(project), []).append(
            (str(event_id), str(kind), start_ts, event_paths)
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    matched_count = 0
    for sha, repo, project, authored_at, paths in commit_rows:
        commit_paths = _bounded_normalized_paths(paths, _MAX_COMMIT_PATHS)
        if not commit_paths:
            continue
        event_ids: list[str] = []
        kinds: list[str] = []
        seen_events: set[str] = set()
        seen_kinds: set[str] = set()
        for event_id, kind, start_ts, event_paths in events_by_project.get(str(project), ()):
            if abs((authored_at - start_ts).total_seconds()) >= time_window_hours * 3600:
                continue
            if not _has_suffix_path_overlap(commit_paths, event_paths):
                continue
            if event_id not in seen_events:
                seen_events.add(event_id)
                event_ids.append(event_id)
            if kind not in seen_kinds:
                seen_kinds.add(kind)
                kinds.append(kind)
        if not event_ids:
            continue
        attribution = json.dumps(
            {
                "matched_events": len(event_ids),
                "top_kinds": kinds[:5],
                "matched_event_ids": event_ids[:20],
                "matched_via": "project_suffix_path_overlap",
                "time_window_hours": time_window_hours,
                "backfilled_at": now_iso,
            }
        )
        conn.execute(
            """
            UPDATE commit_fact
            SET ai_attribution = ?
            WHERE refresh_id = ? AND sha = ? AND repo = ?
            """,
            [attribution, refresh_id, sha, repo],
        )
        matched_count += 1
    return matched_count


def _bounded_normalized_paths(paths: Any, limit: int) -> tuple[str, ...]:
    if not paths:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        value = str(raw or "").strip().lstrip("/")
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
        if len(normalized) >= limit:
            break
    return tuple(normalized)


def _has_suffix_path_overlap(commit_paths: tuple[str, ...], event_paths: tuple[str, ...]) -> bool:
    for commit_path in commit_paths:
        for event_path in event_paths:
            if event_path.endswith(commit_path) or commit_path.endswith(event_path):
                return True
    return False
