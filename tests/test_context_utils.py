"""Tests for pure utility helpers in context and trajectory period modules."""

from __future__ import annotations

import pytest

from lynchpin.context.packet_builders import _top_n
from lynchpin.context.selection import _estimate_tokens
from lynchpin.trajectory.quarter import _quarter_key


# ---------------------------------------------------------------------------
# _quarter_key (quarter.py)
# ---------------------------------------------------------------------------

class TestQuarterKey:
    def test_january_is_q1(self) -> None:
        assert _quarter_key("2026-01") == "2026-Q1"

    def test_march_is_q1(self) -> None:
        assert _quarter_key("2026-03") == "2026-Q1"

    def test_april_is_q2(self) -> None:
        assert _quarter_key("2026-04") == "2026-Q2"

    def test_june_is_q2(self) -> None:
        assert _quarter_key("2026-06") == "2026-Q2"

    def test_july_is_q3(self) -> None:
        assert _quarter_key("2026-07") == "2026-Q3"

    def test_september_is_q3(self) -> None:
        assert _quarter_key("2026-09") == "2026-Q3"

    def test_october_is_q4(self) -> None:
        assert _quarter_key("2026-10") == "2026-Q4"

    def test_december_is_q4(self) -> None:
        assert _quarter_key("2026-12") == "2026-Q4"

    def test_year_preserved(self) -> None:
        assert _quarter_key("2025-07").startswith("2025-")

    def test_output_format(self) -> None:
        result = _quarter_key("2026-03")
        assert result == "2026-Q1"
        assert result[4] == "-"
        assert result[5] == "Q"


# ---------------------------------------------------------------------------
# _top_n (packet_builders.py)
# ---------------------------------------------------------------------------

class TestTopN:
    def test_compact_tier_returns_3(self) -> None:
        items = tuple((f"p{i}", float(i * 3600)) for i in range(10))
        result = _top_n(items, "compact")
        assert len(result) == 3

    def test_standard_tier_returns_5(self) -> None:
        items = tuple((f"p{i}", float(i * 3600)) for i in range(10))
        result = _top_n(items, "standard")
        assert len(result) == 5

    def test_full_tier_returns_10(self) -> None:
        items = tuple((f"p{i}", float(i * 3600)) for i in range(15))
        result = _top_n(items, "full")
        assert len(result) == 10

    def test_seconds_converted_to_hours(self) -> None:
        items = (("project", 7200.0),)
        result = _top_n(items, "compact")
        assert result[0] == ("project", 2.0)  # 7200 / 3600 = 2.0

    def test_hours_rounded_to_2_decimals(self) -> None:
        items = (("project", 3700.0),)  # 3700 / 3600 = 1.02777...
        result = _top_n(items, "compact")
        assert result[0][1] == round(3700.0 / 3600.0, 2)

    def test_fewer_items_than_limit_returns_all(self) -> None:
        items = (("a", 3600.0), ("b", 7200.0))
        result = _top_n(items, "full")
        assert len(result) == 2

    def test_unknown_tier_defaults_to_5(self) -> None:
        items = tuple((f"p{i}", float(i * 3600)) for i in range(10))
        result = _top_n(items, "unknown_tier")
        assert len(result) == 5

    def test_empty_items_returns_empty(self) -> None:
        assert _top_n((), "standard") == []

    def test_order_preserved(self) -> None:
        # Items are already ordered (pre-sorted by caller); _top_n preserves order
        items = (("first", 10800.0), ("second", 7200.0), ("third", 3600.0))
        result = _top_n(items, "standard")
        names = [r[0] for r in result]
        assert names == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# _estimate_tokens (selection.py)
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_dict_returns_at_least_1(self) -> None:
        assert _estimate_tokens({}) >= 1

    def test_larger_payload_gives_larger_count(self) -> None:
        small = {"key": "val"}
        large = {"key": "val", "description": "a" * 200}
        assert _estimate_tokens(large) > _estimate_tokens(small)

    def test_returns_integer(self) -> None:
        result = _estimate_tokens({"x": 1})
        assert isinstance(result, int)

    def test_approximation_based_on_char_count(self) -> None:
        # ~4 chars per token rule
        payload = {"text": "hello world"}
        import json
        json_len = len(json.dumps(payload))
        expected = max(1, json_len // 4)
        assert _estimate_tokens(payload) == expected

    def test_nested_payload_counted(self) -> None:
        payload = {"nested": {"a": [1, 2, 3], "b": "content"}}
        result = _estimate_tokens(payload)
        assert result > 1
