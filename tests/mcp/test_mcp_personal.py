"""Tests for personal.py MCP tools."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


class TestActivitySemanticDaily:
    """Tests for activity_semantic_daily tool."""

    def test_activity_semantic_daily_shape(self):
        """Verify activity_semantic_daily returns correct shape for 3 days."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        # Mock the connection
        mock_rows = [
            (date(2026, 5, 20), "work", 7200.0),      # 120 min
            (date(2026, 5, 20), "social", 1800.0),    # 30 min
            (date(2026, 5, 21), "work", 10800.0),     # 180 min
            (date(2026, 5, 21), "health", 3600.0),    # 60 min
            (date(2026, 5, 22), "learning", 5400.0),  # 90 min
        ]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = mock_rows

            result = activity_semantic_daily(
                start="2026-05-20",
                end="2026-05-22",
                dimension="topic_category",
            )

        # Verify shape: list of dicts with expected keys
        assert isinstance(result, list)
        assert len(result) == 5
        for row in result:
            assert set(row.keys()) == {
                "date",
                "dimension_value",
                "focused_seconds",
                "focused_minutes",
            }

        # Verify calculations: 7200 seconds = 120 minutes
        assert result[0]["focused_seconds"] == 7200.0
        assert result[0]["focused_minutes"] == 120.0
        assert result[0]["dimension_value"] == "work"
        assert result[0]["date"] == "2026-05-20"

    def test_activity_semantic_daily_invalid_dimension(self):
        """Verify invalid dimension raises ValueError."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        with pytest.raises(ValueError, match="dimension must be one of"):
            activity_semantic_daily(
                start="2026-05-20",
                end="2026-05-22",
                dimension="invalid_dim",
            )

    def test_activity_semantic_daily_valid_dimensions(self):
        """Verify all valid dimensions are accepted."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        valid_dims = ["topic_category", "attention_level", "activity", "platform", "mode"]
        mock_rows = [(date(2026, 5, 20), "test", 3600.0)]

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = mock_rows

            for dim in valid_dims:
                result = activity_semantic_daily(
                    start="2026-05-20",
                    end="2026-05-22",
                    dimension=dim,
                )
                assert isinstance(result, list)

    def test_activity_semantic_daily_empty_result(self):
        """Verify empty results are handled correctly."""
        from lynchpin.mcp.tools.personal import activity_semantic_daily

        with patch("lynchpin.substrate.connection.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_conn.execute.return_value.fetchall.return_value = []

            result = activity_semantic_daily(
                start="2026-05-20",
                end="2026-05-22",
                dimension="topic_category",
            )

        assert result == []
