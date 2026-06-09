"""Chisel re-exports — implementation moved to sources layer.

The builder, dataclasses, and configuration now live in
``lynchpin.sources.chisel`` so that ingest modules can import them without
crossing the analysis/ layer boundary.  All public symbols are re-exported
here for backward compatibility.
"""

from __future__ import annotations

# Re-export everything so existing callers (analysis.projects.cli, etc.) work.
from lynchpin.sources.chisel import (  # noqa: F401
    DEFAULT_IGNORE,
    DEFAULT_ISSUE_LIMIT,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SLICE_WORKERS,
    LARGE_SLICE_BYTES,
    OUTPUT_ROOT_DEFAULT,
    REPO_PLANS,
    RepoPlan,
    Slice,
    build_chisel_bundles,
    run_from_cli,
)
