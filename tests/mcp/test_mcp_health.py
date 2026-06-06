"""Tests for health.py MCP tools."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _stub_health_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lynchpin.mcp.tools.health.ensure_substrate_materialized_for_read",
        lambda *, caller, window=None: {
            "name": "evidence_graph_substrate",
            "status": "ready",
            "caller": caller,
            "window": window,
        },
    )


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_substrate_gap_draft_materializes_before_latest_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.health.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: "fixture.duckdb",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.connection.connect",
        lambda *_args, **_kwargs: _Conn(),
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.health.latest_materialized_refresh_id",
        lambda *_args, **_kwargs: None,
    )

    from lynchpin.mcp.tools.health import substrate_gap_draft

    result = substrate_gap_draft()

    assert calls == [("substrate_gap_draft", None)]
    assert result["all_sources_healthy"] is True


def test_substrate_confidence_matrix_materializes_only_for_default_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.health.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: "fixture.duckdb",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.connection.connect",
        lambda *_args, **_kwargs: _Conn(),
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.health.best_materialized_refresh_id",
        lambda *_args, **_kwargs: None,
    )

    from lynchpin.mcp.tools.health import substrate_confidence_matrix

    assert substrate_confidence_matrix() == {"error": "no promote runs"}
    assert calls == [("substrate_confidence_matrix", None)]

    def fail_reader(*_args, **_kwargs):
        raise AssertionError("explicit refresh_id path should not select default snapshot")

    monkeypatch.setattr(
        "lynchpin.substrate.readers_health.load_evidence_node_by_source",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "lynchpin.substrate.readers_health.load_source_status_map",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr("lynchpin.mcp.tools.health.best_materialized_refresh_id", fail_reader)

    result = substrate_confidence_matrix(refresh_id="historical-rid")

    assert calls == [("substrate_confidence_matrix", None)]
    assert result["refresh_id"] == "historical-rid"


def test_work_package_durability_uses_best_symbol_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: "fixture.duckdb",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.connection.connect",
        lambda *_args, **_kwargs: _Conn(),
    )
    monkeypatch.setattr(
        "lynchpin.mcp.tools.health.best_materialized_refresh_id",
        lambda *_args, **_kwargs: calls.append(_args) or None,
    )

    from lynchpin.mcp.tools.health import work_package_durability

    result = work_package_durability()

    assert result == {"error": "no promote runs"}
    assert calls[0][1] == "symbol_change"


def test_health_trend_defaults_to_successful_promotion_runs(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.mcp.conftest import setup_substrate

    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES
              ('rid-old', 'ok', NULL, DATE '2026-05-01', DATE '2026-05-02',
               'materialized', '{}', TIMESTAMPTZ '2026-06-05 10:00:00+00',
               TIMESTAMPTZ '2026-06-05 10:01:00+00'),
              ('rid-new', 'ok', NULL, DATE '2026-05-02', DATE '2026-05-03',
               'materialized', '{}', TIMESTAMPTZ '2026-06-05 11:00:00+00',
               TIMESTAMPTZ '2026-06-05 11:01:00+00')
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status (
                refresh_id, source, kind, status, reason, row_count,
                window_start, window_end, recorded_at
            )
            VALUES
              ('rid-old', 'commits', 'stage', 'ok', NULL, 1,
               DATE '2026-05-01', DATE '2026-05-02', TIMESTAMPTZ '2026-06-05 10:01:00+00'),
              ('rid-new', 'commits', 'stage', 'ok', NULL, 1,
               DATE '2026-05-02', DATE '2026-05-03', TIMESTAMPTZ '2026-06-05 11:01:00+00'),
              ('rid-narrow', 'machine', 'continuous', 'ok', NULL, 1,
               DATE '2026-06-05', DATE '2026-06-05', TIMESTAMPTZ '2026-06-05 12:00:00+00')
            """
        )

    from lynchpin.mcp.tools.health import health_trend

    result = health_trend()

    assert result["prior"] == "rid-old"
    assert result["current"] == "rid-new"


def test_cleanup_period_detect_materializes_requested_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_ensure_substrate_materialized_for_read(*, caller, window=None):
        calls.append((caller, window))
        return {"name": "evidence_graph_substrate", "status": "ready"}

    monkeypatch.setattr(
        "lynchpin.mcp.tools.health.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )

    with patch("lynchpin.substrate.connection.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.side_effect = [
            MagicMock(fetchall=lambda: []),
            MagicMock(fetchall=lambda: []),
        ]

        from lynchpin.mcp.tools.health import cleanup_period_detect

        assert cleanup_period_detect(start="2026-04-01", end="2026-04-30") == []

    assert calls == [
        ("cleanup_period_detect", (date(2026, 4, 1), date(2026, 5, 1)))
    ]


class TestCleanupPeriodDetect:
    """Tests for cleanup_period_detect tool."""

    def test_cleanup_period_detect_shape(self):
        """Verify cleanup_period_detect returns correct shape."""
        from lynchpin.mcp.tools.health import cleanup_period_detect

        # Mock the connection with synthetic data
        # Rows are (year_month, column_value)
        commit_rows = [
            ("2026-04", 100),  # April: 100 commits
            ("2026-05", 50),   # May: 50 commits
        ]
        message_rows = [
            ("2026-04", 600000),  # April: 600k messages (ratio = 6000, cleanup)
            ("2026-05", 500),     # May: 500 messages (ratio = 10, normal)
        ]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn

            # First call is commits query
            # Second call is messages query
            mock_conn.execute.side_effect = [
                MagicMock(fetchall=lambda: commit_rows),
                MagicMock(fetchall=lambda: message_rows),
            ]

            result = cleanup_period_detect(
                start="2026-04-01",
                end="2026-05-31",
            )

        # Verify shape
        assert isinstance(result, list)
        assert len(result) == 2

        for row in result:
            assert set(row.keys()) == {
                "year_month",
                "commit_count",
                "ai_messages",
                "ratio",
                "likely_cleanup",
            }

        # Verify April (cleanup period)
        assert result[0]["year_month"] == "2026-04"
        assert result[0]["commit_count"] == 100
        assert result[0]["ai_messages"] == 600000
        assert result[0]["ratio"] == 6000.0
        assert result[0]["likely_cleanup"] is True

        # Verify May (normal period)
        assert result[1]["year_month"] == "2026-05"
        assert result[1]["commit_count"] == 50
        assert result[1]["ai_messages"] == 500
        assert result[1]["ratio"] == 10.0
        assert result[1]["likely_cleanup"] is False

    def test_cleanup_period_detect_with_project_filter(self):
        """Verify project filter is applied."""
        from lynchpin.mcp.tools.health import cleanup_period_detect

        commit_rows = [("2026-04", 50)]
        message_rows = [("2026-04", 100)]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchall=lambda: commit_rows),
                MagicMock(fetchall=lambda: message_rows),
            ]

            result = cleanup_period_detect(
                start="2026-04-01",
                end="2026-04-30",
                project="sinex",
            )

        assert len(result) == 1
        assert result[0]["year_month"] == "2026-04"
        # Verify that execute was called twice
        assert mock_conn.execute.call_count == 2
        # Check that the project filter was in the SQL
        calls = mock_conn.execute.call_args_list
        assert "sinex" in str(calls[0])  # First call (commits)
        assert "sinex" in str(calls[1])  # Second call (messages)

    def test_cleanup_period_detect_zero_commits(self):
        """Verify handling of months with zero commits."""
        from lynchpin.mcp.tools.health import cleanup_period_detect

        commit_rows = [("2026-04", 0)]
        message_rows = [("2026-04", 1000)]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchall=lambda: commit_rows),
                MagicMock(fetchall=lambda: message_rows),
            ]

            result = cleanup_period_detect(
                start="2026-04-01",
                end="2026-04-30",
            )

        assert len(result) == 1
        # With 0 commits, ratio should be based on max(1, commits) = 1
        assert result[0]["ratio"] == 1000.0
        assert result[0]["likely_cleanup"] is False  # Still below 5000

    def test_cleanup_period_detect_high_ratio_threshold(self):
        """Verify the 5000 threshold for cleanup detection."""
        from lynchpin.mcp.tools.health import cleanup_period_detect

        # Test exactly at threshold and just above
        commit_rows = [("2026-04", 1), ("2026-05", 1)]
        message_rows = [("2026-04", 5000), ("2026-05", 5001)]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchall=lambda: commit_rows),
                MagicMock(fetchall=lambda: message_rows),
            ]

            result = cleanup_period_detect(
                start="2026-04-01",
                end="2026-05-31",
            )

        # 5000:1 is exactly at threshold - should be False
        assert result[0]["likely_cleanup"] is False
        # 5001:1 is above threshold - should be True
        assert result[1]["likely_cleanup"] is True

    def test_cleanup_period_detect_empty_result(self):
        """Verify empty results are handled."""
        from lynchpin.mcp.tools.health import cleanup_period_detect

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.side_effect = [
                MagicMock(fetchall=lambda: []),
                MagicMock(fetchall=lambda: []),
            ]

            result = cleanup_period_detect(
                start="2026-04-01",
                end="2026-04-30",
            )

        assert result == []
