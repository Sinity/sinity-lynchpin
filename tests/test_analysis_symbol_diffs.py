"""Tests for analysis.code_index.symbol_diffs hunk parsing and overlap."""

from __future__ import annotations

from lynchpin.analysis.code_index.symbol_diffs import _overlap, _parse_unified0_diff


def test_parses_unified0_hunk_headers() -> None:
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc..def 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -10,3 +10,5 @@ context\n"
        "+a\n+b\n+c\n"
        "@@ -100 +102,2 @@\n"
        "+x\n+y\n"
        "diff --git a/bar.rs b/bar.rs\n"
        "--- a/bar.rs\n"
        "+++ b/bar.rs\n"
        "@@ -1,2 +1,0 @@\n"
        "-removed1\n-removed2\n"
    )
    hunks = _parse_unified0_diff(diff)
    assert "foo.py" in hunks and "bar.rs" in hunks
    assert hunks["foo.py"][0] == {
        "old_start": 10,
        "old_count": 3,
        "new_start": 10,
        "new_count": 5,
    }
    # Single-line hunks omit the count -> defaults to 1
    assert hunks["foo.py"][1] == {
        "old_start": 100,
        "old_count": 1,
        "new_start": 102,
        "new_count": 2,
    }
    assert hunks["bar.rs"][0]["new_count"] == 0


def test_overlap_clamps_to_intersection() -> None:
    # hunk at lines 10..14 (start=10, count=5), symbol at 8..12 -> overlap 10..12 = 3
    assert _overlap(10, 5, 8, 12) == 3
    # disjoint
    assert _overlap(10, 5, 15, 20) == 0
    # symbol entirely inside hunk
    assert _overlap(10, 10, 12, 15) == 4
    # zero-count hunk (pure deletion on new side / pure addition on old side) -> 0
    assert _overlap(0, 0, 1, 5) == 0
    # invalid symbol range
    assert _overlap(10, 5, 12, 8) == 0
