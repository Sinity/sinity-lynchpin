"""Tests for text-shape helpers used in ecosystem comparison."""

from __future__ import annotations

from lynchpin.analysis._utils.textshape import compute_repetition_metrics, normalize_code_line


def test_normalize_code_line_strips_literals_comments_and_case() -> None:
    line = """Value := "Hello" + Foo(123); // trailing comment"""
    assert normalize_code_line(line) == "value := str + foo( 0 );"


def test_normalize_code_line_keeps_rust_attributes() -> None:
    assert normalize_code_line("#[cfg(test)]") == "#[cfg(test)]"


def test_compute_repetition_metrics_detects_duplicate_heavy_inputs() -> None:
    repeated = compute_repetition_metrics(
        [
            "fn alpha() { return 1; }\nfn alpha() { return 1; }\nfn beta() { return 2; }",
        ]
    )
    varied = compute_repetition_metrics(
        [
            "fn alpha() { return 1; }\nfn beta() { return 2; }\nfn gamma() { return 3; }",
        ]
    )

    assert repeated["normalized_line_count"] == 3
    assert repeated["unique_normalized_lines"] == 2
    assert repeated["line_uniqueness_ratio"] < varied["line_uniqueness_ratio"]
    assert repeated["compression_ratio"] < varied["compression_ratio"]
    assert repeated["top_duplicate_lines"][0]["count"] == 2
