"""AI-attribution backfill for substrate promotion."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _backfill_ai_attribution(
    conn: Any,
    *,
    refresh_id: str,
    time_window_hours: int = 24,
) -> int:
    """Populate commit_fact.ai_attribution from project/path/timestamp overlap."""
    matches = conn.execute(
        """
        WITH candidate_paths AS (
            SELECT
                c.sha,
                c.repo,
                we.event_id,
                we.kind,
                cp.commit_path,
                ep.event_path
            FROM commit_fact c
            JOIN ai_work_event we
              ON c.refresh_id = we.refresh_id
             AND c.project = we.project
             AND ABS(EXTRACT(EPOCH FROM c.authored_at - we.start_ts)) < ?
            , UNNEST(c.paths) AS cp(commit_path)
            , UNNEST(we.file_paths) AS ep(event_path)
            WHERE c.refresh_id = ?
              AND we.start_ts IS NOT NULL
              AND c.project IS NOT NULL
              AND we.project IS NOT NULL
              AND len(c.paths) > 0
              AND len(we.file_paths) > 0
        ),
        path_matches AS (
            SELECT DISTINCT sha, repo, event_id, kind
            FROM candidate_paths
            WHERE
                ends_with(ltrim(event_path, '/'), ltrim(commit_path, '/'))
                OR ends_with(ltrim(commit_path, '/'), ltrim(event_path, '/'))
        )
        SELECT
            sha,
            repo,
            COUNT(DISTINCT event_id) AS matched_events,
            ARRAY_AGG(DISTINCT kind) AS kinds,
            ARRAY_AGG(DISTINCT event_id) AS event_ids
        FROM path_matches
        GROUP BY sha, repo
    """,
        [time_window_hours * 3600, refresh_id],
    ).fetchall()

    now_iso = datetime.now(timezone.utc).isoformat()
    for sha, repo, matched_events, kinds, event_ids in matches:
        attribution = json.dumps(
            {
                "matched_events": int(matched_events),
                "top_kinds": list(kinds[:5]) if kinds else [],
                "matched_event_ids": list(event_ids[:20]) if event_ids else [],
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
    return len(matches)
