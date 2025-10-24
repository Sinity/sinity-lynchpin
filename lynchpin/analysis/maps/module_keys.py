"""Shared path-grouping helpers for analysis map generators."""

from __future__ import annotations

from typing import Literal


Ecosystem = Literal["sinex"]


def _path_parts(rel: str) -> list[str]:
    return [part for part in rel.replace("\\", "/").split("/") if part]


def sinex_module_key(rel: str) -> str:
    parts = _path_parts(rel)
    if not parts:
        return "unknown"
    if parts[0] == "crate":
        if len(parts) >= 4 and parts[1] in {"lib", "bin"}:
            return f"{parts[0]}/{parts[1]}/{parts[2]}"
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    if parts[0] in {"tests", "tools", "scripts"}:
        return parts[0]
    return parts[0]


def is_test_path(rel: str, ecosystem: Ecosystem) -> bool:
    path = rel.replace("\\", "/").lower()
    return "/tests/" in path or path.startswith("tests/") or path.endswith("_test.rs")
