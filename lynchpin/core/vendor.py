"""Helpers for vendored third-party sources (e.g. upstream HPI forks)."""

from __future__ import annotations

import sys
from pathlib import Path


def add_vendor_paths() -> None:
    """Ensure vendored HPI trees are importable as regular packages."""

    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "external" / "hpi",
        root / "external" / "hpi-madelinecameron",
        root / "external" / "hpi-purarue",
        root / "external" / "hpi-sinity",
    ]
    vendor_roots = [_resolve_vendor_root(path) for path in candidates]
    for path in reversed(vendor_roots):
        if path.exists():
            path_str = str(path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)


def _resolve_vendor_root(root: Path) -> Path:
    if (root / "my").exists():
        return root
    if (root / "src" / "my").exists():
        return root / "src"
    return root
