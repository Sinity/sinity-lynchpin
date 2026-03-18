"""Tests for pure text-parsing helpers in lynchpin/system/ modules."""

from __future__ import annotations

import pytest

from lynchpin.system.meta import _first_heading
from lynchpin.system.sinex import _first_docstring
from lynchpin.system.sinnix import _leading_comment


# ---------------------------------------------------------------------------
# _first_heading (meta.py)
# ---------------------------------------------------------------------------

class TestFirstHeading:
    def test_h1_heading_extracted(self) -> None:
        text = "# My Title\nsome content\n"
        assert _first_heading(text) == "My Title"

    def test_h2_heading_extracted(self) -> None:
        text = "## Section\ncontent\n"
        assert _first_heading(text) == "Section"

    def test_no_heading_returns_none(self) -> None:
        assert _first_heading("just text\nno headings\n") is None

    def test_empty_string_returns_none(self) -> None:
        assert _first_heading("") is None

    def test_first_heading_returned_when_multiple(self) -> None:
        text = "# First\n# Second\n"
        assert _first_heading(text) == "First"

    def test_leading_hashes_stripped(self) -> None:
        text = "### Deep Heading\n"
        assert _first_heading(text) == "Deep Heading"

    def test_hash_in_middle_of_line_not_heading(self) -> None:
        text = "not # a heading\n# Real Heading\n"
        # Line "not # a heading" doesn't start with #, so skipped
        assert _first_heading(text) == "Real Heading"


# ---------------------------------------------------------------------------
# _leading_comment (sinnix.py)
# ---------------------------------------------------------------------------

class TestLeadingComment:
    def test_hash_comment_extracted(self) -> None:
        text = "# This is a comment\nlet x = 1;\n"
        result = _leading_comment(text)
        assert result == "This is a comment"

    def test_double_slash_comment_extracted(self) -> None:
        text = "// A Rust comment\npub fn main() {}\n"
        result = _leading_comment(text)
        assert result == "A Rust comment"

    def test_multiple_comment_lines_joined(self) -> None:
        text = "# Line one\n# Line two\ncode\n"
        result = _leading_comment(text)
        assert "Line one" in result
        assert "Line two" in result

    def test_non_comment_line_stops_extraction(self) -> None:
        text = "# Comment\ncode line\n# after code"
        result = _leading_comment(text)
        assert result == "Comment"
        # "after code" comes after a code line, so not included
        assert "after code" not in (result or "")

    def test_empty_string_returns_none(self) -> None:
        assert _leading_comment("") is None

    def test_no_comments_returns_none(self) -> None:
        assert _leading_comment("let x = 1;\nlet y = 2;\n") is None

    def test_blank_lines_between_comments_included(self) -> None:
        # Blank lines between comments are allowed
        text = "# First\n\n# Second\ncode\n"
        result = _leading_comment(text)
        assert result is not None
        assert "First" in result
        assert "Second" in result

    def test_c_block_comment_extracted(self) -> None:
        text = "/* Module overview */\ncode\n"
        result = _leading_comment(text)
        assert result is not None
        assert "Module overview" in result


# ---------------------------------------------------------------------------
# _first_docstring (system/sinex.py) — extracts first Rust /// doc comment
# ---------------------------------------------------------------------------

class TestFirstDocstring:
    def test_triple_slash_comment_extracted(self) -> None:
        text = "/// Node handler\npub fn handle() {}\n"
        assert _first_docstring(text) == "Node handler"

    def test_no_doc_comment_returns_empty(self) -> None:
        assert _first_docstring("pub fn main() {}\n") == ""

    def test_empty_string_returns_empty(self) -> None:
        assert _first_docstring("") == ""

    def test_first_doc_comment_returned(self) -> None:
        text = "/// First doc\n/// Second doc\n"
        assert _first_docstring(text) == "First doc"

    def test_regular_comment_ignored(self) -> None:
        # // is not ///, so it should not be extracted
        text = "// plain comment\n/// doc comment\n"
        assert _first_docstring(text) == "doc comment"

    def test_leading_slashes_stripped(self) -> None:
        result = _first_docstring("/// Leading\n")
        assert not result.startswith("/")
        assert result == "Leading"
