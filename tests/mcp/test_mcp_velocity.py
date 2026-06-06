from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

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
    assert narrative["materialized_refresh_id"] == "graph-refresh"
    assert narrative["refresh_id"] == "graph-refresh"
    assert narrative["materialization"]["name"] == "evidence_graph_substrate"
    assert narrative["materialization"]["caller"] == "velocity_narrative"
    assert narrative["total_commits"] == 1


def _insert_commit_fact(conn, *, sha: str, project: str, authored_at, refresh_id: str) -> None:
    conn.execute(
        """
        INSERT INTO commit_fact (
            sha, repo, project, authored_at, lines_added, lines_deleted,
            lines_changed, files_changed, paths, path_roots, breaking_change,
            categories, change_types, classified_files_changed, parent_count,
            refresh_id, materialized_at
        ) VALUES (?, ?, ?, ?, 0, 0, 0, 0, [], [], FALSE, '[]', '[]', 0, 1, ?, ?)
        """,
        [sha, project, project, authored_at, refresh_id, dt(2026, 5, 10)],
    )


def _insert_evidence_commit(conn, *, node_id: str, project: str, day: date, refresh_id: str) -> None:
    conn.execute(
        """
        INSERT INTO evidence_node (
            refresh_id, id, kind, source, date, project,
            summary, payload, caveats
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            node_id,
            "commit",
            "git",
            day,
            project,
            "commit",
            json.dumps({"commit": node_id}),
            "[]",
        ],
    )


def _insert_symbol_change(
    conn,
    *,
    sha: str,
    project: str,
    day: date,
    qualified_name: str,
    change_type: str,
    refresh_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO symbol_change (
            sha, project, date, path, change_type, qualified_name,
            symbol_kind, exported, breaking_candidate, refresh_id, materialized_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'function', FALSE, FALSE, ?, ?)
        """,
        [
            sha,
            project,
            day,
            f"{project}/src/lib.rs",
            change_type,
            qualified_name,
            refresh_id,
            dt(2026, 5, 10),
        ],
    )


def _fail_if_materialized(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("explicit refresh_id reads must not converge materialization")


def test_velocity_series_pinned_refresh_id_does_not_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    import lynchpin.mcp.tools.velocity as velocity_tools
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        _insert_evidence_commit(
            conn,
            node_id="git:lynchpin:pinned",
            project="lynchpin",
            day=date(2026, 5, 4),
            refresh_id="pinned",
        )

    monkeypatch.setattr(velocity_tools, "ensure_substrate_materialized_for_read", _fail_if_materialized)

    rows = velocity_tools.velocity_series(refresh_id="pinned")

    assert rows == [
        {
            "project": "lynchpin",
            "date": "2026-05-04",
            "commit_count": 1,
            "rolling_avg": 1.0,
            "cumulative": 1,
            "source_count": 1,
        }
    ]


def test_velocity_narrative_pinned_refresh_id_does_not_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    import lynchpin.mcp.tools.velocity as velocity_tools
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        _insert_evidence_commit(
            conn,
            node_id="git:lynchpin:pinned",
            project="lynchpin",
            day=date(2026, 5, 4),
            refresh_id="pinned",
        )

    monkeypatch.setattr(velocity_tools, "ensure_substrate_materialized_for_read", _fail_if_materialized)

    result = velocity_tools.velocity_narrative(refresh_id="pinned")

    assert result["materialized_refresh_id"] == "pinned"
    assert result["refresh_id"] == "pinned"
    assert result["total_commits"] == 1
    assert result["materialization"]["status"] == "pinned"
    assert result["materialization"]["caller"] == "velocity_narrative"


def test_symbol_velocity_pinned_refresh_id_does_not_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    import lynchpin.mcp.tools.velocity as velocity_tools
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        _insert_symbol_change(
            conn,
            sha="a",
            project="lynchpin",
            day=date(2026, 5, 4),
            qualified_name="lynchpin.core.config.get_config",
            change_type="ADDED",
            refresh_id="pinned",
        )

    monkeypatch.setattr(velocity_tools, "ensure_substrate_materialized_for_read", _fail_if_materialized)

    rows = velocity_tools.symbol_velocity(refresh_id="pinned")

    assert rows == [
        {
            "project": "lynchpin",
            "date": "2026-05-04",
            "materialized_refresh_id": "pinned",
            "commit_count": 0,
            "symbols_added": 1,
            "symbols_modified": 0,
            "symbols_renamed": 0,
            "symbols_total": 1,
        }
    ]


def test_temporal_rhythm_pinned_refresh_id_does_not_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    import lynchpin.mcp.tools.velocity as velocity_tools
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        _insert_commit_fact(
            conn,
            sha="a",
            project="lynchpin",
            authored_at=dt(2026, 5, 4, 2),
            refresh_id="pinned",
        )

    monkeypatch.setattr(velocity_tools, "ensure_substrate_materialized_for_read", _fail_if_materialized)

    result = velocity_tools.temporal_rhythm(refresh_id="pinned")

    assert sum(r["count"] for r in result["hourly"]) == 1
    assert sum(r["count"] for r in result["weekday"]) == 1
    assert result["materialized_refresh_id"] == "pinned"
    assert result["materialization"]["status"] == "pinned"
    assert result["materialization"]["caller"] == "temporal_rhythm"


def test_engineering_throughput_pinned_refresh_id_does_not_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    import lynchpin.mcp.tools.velocity as velocity_tools
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        _insert_commit_fact(
            conn,
            sha="a",
            project="lynchpin",
            authored_at=dt(2026, 5, 4, 2),
            refresh_id="pinned",
        )

    monkeypatch.setattr(velocity_tools, "ensure_substrate_materialized_for_read", _fail_if_materialized)

    result = velocity_tools.engineering_throughput(project="lynchpin", refresh_id="pinned")

    assert result["materialized_refresh_id"] == "pinned"
    assert result["refresh_id"] == "pinned"
    assert len(result["periods"]) == 1
    assert result["materialization"]["status"] == "pinned"
    assert result["materialization"]["caller"] == "engineering_throughput"


def test_engineering_throughput_materializes_half_open_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.mcp.tools.velocity as velocity_tools

    calls = []

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready", "caller": caller}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        velocity_tools,
        "ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    monkeypatch.setattr(
        "lynchpin.substrate.readers_velocity.load_best_coverage_refresh_id",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(velocity_tools, "best_materialized_refresh_id", lambda *_args, **_kwargs: None)

    result = velocity_tools.engineering_throughput(
        project="lynchpin",
        start="2026-05-01",
        end="2026-05-03",
    )

    assert result["degraded"] is True
    assert calls == [("engineering_throughput", (date(2026, 5, 1), date(2026, 5, 4)))]


def test_symbol_velocity_uses_symbol_refresh_not_global_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.velocity import symbol_velocity
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        _insert_symbol_change(
            conn,
            sha="a",
            project="lynchpin",
            day=date(2026, 5, 4),
            qualified_name="lynchpin.core.config.get_config",
            change_type="ADDED",
            refresh_id="dag:symbols",
        )
        _insert_symbol_change(
            conn,
            sha="b",
            project="lynchpin",
            day=date(2026, 5, 4),
            qualified_name="lynchpin.core.config.LynchpinConfig",
            change_type="MODIFIED",
            refresh_id="dag:symbols",
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, status, reason, row_count,
                window_start, window_end, recorded_at
            ) VALUES
              (?, ?, ?, ?, ?, ?, ?, ?),
              (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "dag:symbols",
                "symbols",
                "ok",
                None,
                2,
                date(2026, 5, 1),
                date(2026, 5, 5),
                dt(2026, 5, 5),
                "machine-analysis:latest",
                "machine",
                "ok",
                None,
                1,
                date(2026, 6, 1),
                date(2026, 6, 2),
                dt(2026, 6, 5),
            ],
        )

    rows = symbol_velocity()

    assert rows == [
        {
            "project": "lynchpin",
            "date": "2026-05-04",
            "materialized_refresh_id": "dag:symbols",
            "commit_count": 0,
            "symbols_added": 1,
            "symbols_modified": 1,
            "symbols_renamed": 0,
            "symbols_total": 2,
        }
    ]


def test_temporal_rhythm_uses_commit_fact_refresh_not_global_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: temporal_rhythm must resolve the refresh that actually has
    commit_fact rows, not the globally-latest refresh (which may belong to a
    different namespace, e.g. machine-analysis, with zero commit_fact rows).
    Previously it selected the globally latest substrate snapshot and returned
    all-empty distributions despite thousands of promoted commits."""
    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.mcp.tools.velocity import temporal_rhythm
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        # commit_fact rows live under a dag refresh ...
        _insert_commit_fact(conn, sha="a", project="lynchpin", authored_at=dt(2026, 5, 4, 2), refresh_id="dag:2026-05-04")
        _insert_commit_fact(conn, sha="b", project="lynchpin", authored_at=dt(2026, 5, 4, 2), refresh_id="dag:2026-05-04")
        _insert_commit_fact(conn, sha="c", project="sinex", authored_at=dt(2026, 5, 6, 14), refresh_id="dag:2026-05-04")
        # ... while a NEWER refresh in another namespace has no commit_fact rows.
        # The old (buggy) global-latest resolver would select this and return empty.
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, status, reason, row_count,
                window_start, window_end, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["machine-analysis:rolling:today", "machine", "ok", None, 1,
             date(2026, 6, 1), date(2026, 6, 2), dt(2026, 6, 5)],
        )

    result = temporal_rhythm()
    # The regression: non-empty distributions over all promoted commits, despite
    # a newer non-commit refresh existing. (Hour values are tz-dependent, so we
    # assert structure/counts, not literal clock hours.)
    assert sum(r["count"] for r in result["hourly"]) == 3
    assert sum(r["count"] for r in result["weekday"]) == 3
    assert len(result["hourly"]) == 2  # two distinct commit hours (a/b share one, c the other)
    assert max(r["count"] for r in result["hourly"]) == 2  # the doubled hour is the peak
    assert result["peak_hour"] is not None
    assert result["materialized_refresh_id"] == "dag:2026-05-04"
    assert result["refresh_id"] == "dag:2026-05-04"
    assert result["materialization"]["name"] == "evidence_graph_substrate"
    assert result["materialization"]["caller"] == "temporal_rhythm"

    sinex_only = temporal_rhythm(project="sinex")
    assert sum(r["count"] for r in sinex_only["hourly"]) == 1
    assert temporal_rhythm(project="nonexistent")["hourly"] == []
