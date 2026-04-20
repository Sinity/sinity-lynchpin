"""Text normalization and repetition metrics for code-shape comparisons."""

from __future__ import annotations

import re
import zlib
from collections import Counter
from typing import Iterable

_PASCAL_INLINE_COMMENT_RE = re.compile(r"\{[^{}]*\}|\(\*.*?\*\)")
_STRING_RE = re.compile(r"""(\"([^\"\\]|\\.)*\"|'([^'\\]|\\.)*')""")
_NUMBER_RE = re.compile(r"\b\d+(?:[._]\d+)*\b")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_code_line(line: str) -> str:
    """Collapse a code line into a language-agnostic comparison token."""
    normalized = line.strip()
    if not normalized:
        return ""
    if normalized.startswith("#") and not normalized.startswith("#["):
        return ""
    normalized = re.sub(r"//.*$", " ", normalized)
    normalized = _PASCAL_INLINE_COMMENT_RE.sub(" ", normalized)
    normalized = _STRING_RE.sub(" STR ", normalized)
    normalized = _NUMBER_RE.sub(" 0 ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip().lower()
    return normalized


def compute_repetition_metrics(texts: Iterable[str], *, top_n: int = 8) -> dict[str, object]:
    """Compute compactness / repetition metrics over a sequence of source texts."""
    normalized_lines: list[str] = []
    for text in texts:
        for raw_line in text.splitlines():
            line = normalize_code_line(raw_line)
            if len(line) >= 3:
                normalized_lines.append(line)

    if not normalized_lines:
        return {
            "normalized_line_count": 0,
            "unique_normalized_lines": 0,
            "line_uniqueness_ratio": 0.0,
            "duplicate_line_share": 0.0,
            "compression_ratio": 0.0,
            "top_duplicate_lines": [],
        }

    counts = Counter(normalized_lines)
    total = len(normalized_lines)
    unique = len(counts)
    blob = "\n".join(normalized_lines).encode("utf-8")
    compressed = zlib.compress(blob, level=9)

    return {
        "normalized_line_count": total,
        "unique_normalized_lines": unique,
        "line_uniqueness_ratio": round(unique / total, 6),
        "duplicate_line_share": round(1.0 - (unique / total), 6),
        "compression_ratio": round(len(compressed) / max(len(blob), 1), 6),
        "top_duplicate_lines": [
            {"line": line, "count": count}
            for line, count in counts.most_common(top_n)
            if count > 1
        ],
    }
