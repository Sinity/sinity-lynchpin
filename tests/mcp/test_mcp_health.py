"""Tests for health.py MCP tools."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


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
