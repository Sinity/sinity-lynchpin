"""Chisel re-exports — implementation moved to sources layer.

The builder, dataclasses, and configuration live in ``lynchpin.sources.chisel``
so ingest modules can import them without crossing the analysis/ layer
boundary.  This module is the project-analysis facade for that implementation.
"""

from __future__ import annotations

from lynchpin.sources.chisel import (  # noqa: F401
    DEFAULT_IGNORE,
    DEFAULT_ISSUE_LIMIT,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SLICE_WORKERS,
    LARGE_SLICE_BYTES,
    REPO_PLANS,
    RepoPlan,
    Slice,
    StatsBucket,
    build_chisel_bundles,
    run_from_cli,
)
