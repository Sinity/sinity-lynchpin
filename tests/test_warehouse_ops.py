"""Tests for lynchpin.views.warehouse.ops helper functions."""

from __future__ import annotations

import pytest

pytest.importorskip("duckdb", exc_type=ImportError)

from lynchpin.views.warehouse.ops import _extract_col_names, _filtered_source_specs  # noqa: E402


class TestExtractColNames:
    def test_handles_quoted_columns(self) -> None:
        sql = 'CREATE TABLE t (bucket TEXT, start TIMESTAMP, "end" TIMESTAMP, data TEXT)'
        assert _extract_col_names(sql) == ["bucket", "start", "end", "data"]

    def test_simple(self) -> None:
        sql = "CREATE TABLE t (id BIGINT, name TEXT)"
        assert _extract_col_names(sql) == ["id", "name"]

    def test_no_match_returns_empty(self) -> None:
        sql = "SELECT 1"
        assert _extract_col_names(sql) == []

    def test_single_column(self) -> None:
        sql = "CREATE TABLE t (id BIGINT)"
        assert _extract_col_names(sql) == ["id"]


class TestFilteredSourceSpecs:
    def test_filters_to_selected_table(self) -> None:
        specs = _filtered_source_specs(tables=["processed_chat_activity"])

        assert len(specs) == 1
        assert specs[0].name == "processed"
        assert [table.name for table in specs[0].tables] == ["processed_chat_activity"]

    def test_unknown_table_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown warehouse table"):
            _filtered_source_specs(tables=["does_not_exist"])
