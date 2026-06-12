from __future__ import annotations

import json
from datetime import datetime, timezone


UTC = timezone.utc


def test_ai_attribution_backfill_matches_without_path_cross_product() -> None:
    import duckdb

    from lynchpin.analysis.active.substrate_promote_backfill import _backfill_ai_attribution
    from lynchpin.substrate.connection import apply_schema

    conn = duckdb.connect(":memory:")
    apply_schema(conn)
    conn.execute(
        """
        INSERT INTO commit_fact (
            sha, repo, project, authored_at, author, subject, paths, refresh_id
        )
        VALUES
            (
                'abc', 'repo', 'sinity-lynchpin',
                ?, 'Tester', 'fix reader',
                ['lynchpin/sources/polylogue.py'],
                'dag:test'
            ),
            (
                'def', 'repo', 'sinex',
                ?, 'Tester', 'other work',
                ['sinex/src/main.rs'],
                'dag:test'
            )
        """,
        [
            datetime(2026, 6, 1, 12, tzinfo=UTC),
            datetime(2026, 6, 1, 12, tzinfo=UTC),
        ],
    )
    conn.execute(
        """
        INSERT INTO ai_work_event (
            event_id, conversation_id, provider, project, kind,
            kind_confidence, file_paths, tools_used, start_ts,
            duration_ms, summary, refresh_id
        )
        VALUES
            (
                'ev1', 'session-1', 'codex', 'sinity-lynchpin', 'debugging',
                0.9,
                [
                    '/tmp/noise-1',
                    '/realm/project/sinity-lynchpin/lynchpin/sources/polylogue.py',
                    '/tmp/noise-2'
                ],
                ['apply_patch'],
                ?, 1000, 'fixed direct reader', 'dag:test'
            ),
            (
                'ev2', 'session-2', 'codex', 'sinity-lynchpin', 'review',
                0.7,
                ['/realm/project/sinity-lynchpin/README.md'],
                ['read'],
                ?, 1000, 'unrelated path', 'dag:test'
            )
        """,
        [
            datetime(2026, 6, 1, 12, 30, tzinfo=UTC),
            datetime(2026, 6, 1, 12, 30, tzinfo=UTC),
        ],
    )

    assert _backfill_ai_attribution(conn, refresh_id="dag:test") == 1

    rows = conn.execute(
        """
        SELECT sha, CAST(ai_attribution AS VARCHAR)
        FROM commit_fact
        ORDER BY sha
        """
    ).fetchall()

    attribution = json.loads(rows[0][1])
    assert rows[0][0] == "abc"
    assert attribution["matched_events"] == 1
    assert attribution["top_kinds"] == ["debugging"]
    assert attribution["matched_event_ids"] == ["ev1"]
    assert rows[1] == ("def", None)
