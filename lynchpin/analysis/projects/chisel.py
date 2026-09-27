"""Analysis entry point for Chisel builds and source-package adapters."""

from __future__ import annotations

from typing import Any

from lynchpin.analysis.projects.chisel_reports import build_reports
from lynchpin.analysis.projects import chisel_build
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
)

from lynchpin.cli.chisel import main as run_from_cli  # noqa: F401


def build_chisel_bundles(**kwargs: Any) -> dict[str, Any]:
    return chisel_build.build_chisel_bundles(report_builder=build_reports, **kwargs)
