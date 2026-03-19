"""Tests for pure helpers in lynchpin/retrospective/life_outputs.py."""

from __future__ import annotations

import pytest

from lynchpin.retrospective.life_outputs import (
    as_float,
    as_int,
    format_pairs,
    md_inline_code,
)


# ---------------------------------------------------------------------------
# _as_int
# ---------------------------------------------------------------------------

class TestAsInt:
    def test_none_returns_zero(self) -> None:
        assert as_int(None) == 0

    def test_int_passthrough(self) -> None:
        assert as_int(42) == 42

    def test_float_truncated(self) -> None:
        assert as_int(3.9) == 3

    def test_string_integer_parsed(self) -> None:
        assert as_int("17") == 17

    def test_invalid_string_returns_zero(self) -> None:
        assert as_int("not a number") == 0

    def test_bool_true_is_one(self) -> None:
        assert as_int(True) == 1

    def test_bool_false_is_zero(self) -> None:
        assert as_int(False) == 0

    def test_negative_integer(self) -> None:
        assert as_int(-5) == -5


# ---------------------------------------------------------------------------
# _as_float
# ---------------------------------------------------------------------------

class TestAsFloat:
    def test_none_returns_none(self) -> None:
        assert as_float(None) is None

    def test_int_converted(self) -> None:
        assert as_float(3) == 3.0

    def test_float_passthrough(self) -> None:
        assert as_float(3.14) == pytest.approx(3.14)

    def test_string_float_parsed(self) -> None:
        assert as_float("2.5") == pytest.approx(2.5)

    def test_invalid_string_returns_none(self) -> None:
        assert as_float("not a number") is None

    def test_bool_true_is_one(self) -> None:
        assert as_float(True) == 1.0

    def test_returns_float_type(self) -> None:
        result = as_float(42)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# _md_inline_code
# ---------------------------------------------------------------------------

class TestMdInlineCode:
    def test_simple_text_wrapped(self) -> None:
        result = md_inline_code("hello")
        assert result == "`hello`"

    def test_text_with_backtick_uses_double_fence(self) -> None:
        # Text contains `, so fence must be ``
        result = md_inline_code("x`y")
        assert result.startswith("``")
        assert result.endswith("``")
        assert "x`y" in result

    def test_empty_string_returns_backtick_wrapped(self) -> None:
        result = md_inline_code("")
        assert result == "``"

    def test_none_like_empty(self) -> None:
        # `text = text or ""` handles falsy by treating as empty
        result = md_inline_code(None)  # type: ignore[arg-type]
        assert result == "``"

    def test_double_backtick_in_text_uses_triple_fence(self) -> None:
        result = md_inline_code("x``y")
        assert result.startswith("```")


# ---------------------------------------------------------------------------
# _fmt_pairs
# ---------------------------------------------------------------------------

class TestFmtPairs:
    def test_basic_pairs_formatted(self) -> None:
        result = format_pairs([["rust", 5], ["python", 3]])
        assert "rust 5" in result
        assert "python 3" in result

    def test_pairs_joined_by_semicolon(self) -> None:
        result = format_pairs([["a", 1], ["b", 2]])
        assert ";" in result

    def test_non_list_returns_empty(self) -> None:
        assert format_pairs(None) == ""
        assert format_pairs("not a list") == ""

    def test_limit_applied(self) -> None:
        result = format_pairs([["a", 1], ["b", 2], ["c", 3]], limit=2)
        assert "c" not in result

    def test_invalid_item_skipped(self) -> None:
        result = format_pairs([["valid", 5], "invalid_item"])
        assert "valid 5" in result

    def test_wrap_label_adds_backticks(self) -> None:
        result = format_pairs([["rust", 5]], wrap_label=True)
        assert "`rust`" in result

    def test_empty_list_returns_empty(self) -> None:
        assert format_pairs([]) == ""

    def test_item_with_none_label(self) -> None:
        result = format_pairs([[None, 10]])
        assert "10" in result
