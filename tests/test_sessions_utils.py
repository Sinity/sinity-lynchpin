"""Tests for lynchpin.sources.indices.sessions pure string helpers."""

from __future__ import annotations

import pytest

from lynchpin.sources.indices.sessions import (
    _clean_inline,
    _extract_bullets,
    _parse_markdown_sections,
)


# ---------------------------------------------------------------------------
# _clean_inline
# ---------------------------------------------------------------------------

class TestCleanInline:
    def test_backticks_removed(self) -> None:
        assert _clean_inline("`foo`") == "foo"

    def test_asterisks_removed(self) -> None:
        assert _clean_inline("**bold** text") == "bold text"

    def test_whitespace_normalized(self) -> None:
        assert _clean_inline("a   b\t c") == "a b c"

    def test_plain_string_unchanged(self) -> None:
        assert _clean_inline("hello world") == "hello world"

    def test_empty_string_returns_empty(self) -> None:
        assert _clean_inline("") == ""

    def test_only_backticks_returns_empty(self) -> None:
        assert _clean_inline("``") == ""

    def test_mixed_markers(self) -> None:
        result = _clean_inline("`code` and **bold**")
        assert result == "code and bold"


# ---------------------------------------------------------------------------
# _parse_markdown_sections
# ---------------------------------------------------------------------------

class TestParseMarkdownSections:
    def test_empty_text_returns_empty_dict(self) -> None:
        assert _parse_markdown_sections("") == {}

    def test_single_section_captured(self) -> None:
        text = "## Summary\nLine one\nLine two\n"
        sections = _parse_markdown_sections(text)
        assert "Summary" in sections
        assert sections["Summary"] == ["Line one", "Line two"]

    def test_multiple_sections(self) -> None:
        text = "## A\nfoo\n## B\nbar\n"
        sections = _parse_markdown_sections(text)
        assert set(sections.keys()) >= {"A", "B"}

    def test_content_before_first_section_ignored(self) -> None:
        text = "preamble\n## A\nfoo\n"
        sections = _parse_markdown_sections(text)
        assert "preamble" not in sections
        assert "A" in sections

    def test_h1_header_not_treated_as_section(self) -> None:
        text = "# Title\n## Section\nline\n"
        sections = _parse_markdown_sections(text)
        assert "Title" not in sections
        assert "Section" in sections

    def test_section_with_no_content(self) -> None:
        text = "## Empty\n## Next\ndata\n"
        sections = _parse_markdown_sections(text)
        assert "Empty" in sections
        assert sections["Empty"] == []

    def test_trailing_spaces_stripped_from_section_content(self) -> None:
        text = "## A\nline with trailing   \n"
        sections = _parse_markdown_sections(text)
        # rstrip applied — no trailing whitespace in lines
        assert sections["A"] == ["line with trailing"]


# ---------------------------------------------------------------------------
# _extract_bullets
# ---------------------------------------------------------------------------

class TestExtractBullets:
    def test_empty_lines_returns_empty(self) -> None:
        assert _extract_bullets([]) == []

    def test_dash_bullet_extracted(self) -> None:
        result = _extract_bullets(["- item one", "- item two"])
        assert result == ["item one", "item two"]

    def test_star_bullet_extracted(self) -> None:
        result = _extract_bullets(["* item"])
        assert result == ["item"]

    def test_numbered_bullet_extracted(self) -> None:
        # The condition is stripped[:2].isdigit() — requires 2-digit prefix.
        # "10. item": "10".isdigit() → True; stripped[2:].lstrip() starts with "."
        result = _extract_bullets(["10. first item"])
        assert result == ["first item"]

    def test_backtick_stripped_from_bullet(self) -> None:
        result = _extract_bullets(["- `code` value"])
        assert result == ["code value"]

    def test_hash_line_stops_extraction(self) -> None:
        result = _extract_bullets(["- item", "# heading", "- after"])
        assert result == ["item"]

    def test_code_fence_stops_extraction(self) -> None:
        result = _extract_bullets(["- item", "```", "- after"])
        assert result == ["item"]

    def test_plain_text_before_any_bullet_becomes_bullet(self) -> None:
        result = _extract_bullets(["plain text"])
        assert result == ["plain text"]

    def test_continuation_line_appended_to_previous_bullet(self) -> None:
        # Non-bullet plain line after a bullet → continuation
        result = _extract_bullets(["- first", "continuation"])
        assert len(result) == 1
        assert "first" in result[0]
        assert "continuation" in result[0]

    def test_whitespace_only_line_excluded(self) -> None:
        result = _extract_bullets(["   "])
        assert result == []

    def test_multiple_two_digit_numbered_bullets(self) -> None:
        # 2-digit numbers match the stripped[:2].isdigit() condition
        lines = ["10. alpha", "11. beta", "12. gamma"]
        result = _extract_bullets(lines)
        assert result == ["alpha", "beta", "gamma"]
