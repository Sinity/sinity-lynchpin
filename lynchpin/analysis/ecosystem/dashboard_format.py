"""Formatting helpers for ecosystem dashboard payloads and HTML."""

from __future__ import annotations

from typing import Any


def format_number(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if abs(value) >= 100:
            return f"{value:,.1f}"
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)
