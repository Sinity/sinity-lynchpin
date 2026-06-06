from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def test_kind_audit_reports_source_label_disagreements(
    tmp_path: Path, monkeypatch
) -> None:
    from lynchpin.mcp.tools.health import kind_audit
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "substrate.duckdb"
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: db)

    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, kind, status, reason, row_count,
                window_start, window_end, recorded_at
            )
            VALUES
                ('r1', 'ai_attribution', 'stage', 'ok', NULL, 2,
                 DATE '2026-05-25', DATE '2026-05-25', ?),
                ('machine-analysis:latest', 'machine', 'stage', 'ok', NULL, 1,
                 DATE '2026-06-01', DATE '2026-06-01', ?)
            """,
            [
                datetime(2026, 5, 25, tzinfo=timezone.utc),
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            ],
        )
        conn.execute(
            """
            INSERT INTO ai_work_event (
                event_id, conversation_id, provider, kind, kind_confidence,
                kind_tier, kind_source, source_kind, source_confidence,
                overlay_kind, overlay_confidence, file_paths, tools_used,
                duration_ms, refresh_id
            )
            VALUES
                ('e1', 'c1', 'claude-code', 'implementation', 0.9,
                 'high', 'agreement', 'implementation', 0.7,
                 'implementation', 0.85, [], [], 1000, 'r1'),
                ('e2', 'c2', 'claude-code', 'testing', 0.8,
                 'high', 'disagreement', 'implementation', 0.55,
                 'testing', 0.8, [], [], 1000, 'r1')
            """
        )

    audit = kind_audit()

    assert audit["refresh_id"] == "r1"
    assert audit["total"] == 2
    assert audit["source_distribution"] == {"agreement": 1, "disagreement": 1}
    assert audit["disagreement_rate"] == 0.5
    assert audit["top_disagreements"] == [
        {
            "kind": "testing",
            "source_kind": "implementation",
            "overlay_kind": "testing",
            "count": 1,
        }
    ]
