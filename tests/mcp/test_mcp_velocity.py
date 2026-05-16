from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tests.mcp.conftest import dt, setup_substrate


def test_velocity_tools_use_graph_refresh_when_status_refresh_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.velocity import velocity_narrative, velocity_series
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, status, reason, row_count,
                window_start, window_end, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["status-refresh", "commits", "ok", None, 1, date(2026, 5, 1), date(2026, 5, 2), dt(2026, 5, 3)],
        )
        conn.execute(
            """
            INSERT INTO evidence_node (
                refresh_id, id, kind, source, date, project,
                summary, payload, caveats
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "graph-refresh",
                "git:lynchpin:a",
                "commit",
                "git",
                date(2026, 5, 2),
                "lynchpin",
                "commit",
                json.dumps({"commit": "a"}),
                "[]",
            ],
        )

    series = velocity_series()
    narrative = velocity_narrative()

    assert series[0]["project"] == "lynchpin"
    assert series[0]["commit_count"] == 1
    assert narrative["total_commits"] == 1
