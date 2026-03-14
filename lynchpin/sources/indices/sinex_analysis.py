"""Source module for Sinex codebase analysis metrics.

Reads from artefacts/analysis/derived/sinex_structure_metrics.json and
sinex_temporal_metrics.json, yielding typed dataclass records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from ...core.config import get_config

_DERIVED_DIR = "artefacts/analysis/derived"


def _derived_path(filename: str) -> Path:
    cfg = get_config()
    return cfg.repo_root / _DERIVED_DIR / filename


@dataclass
class SinexCrateMetric:
    crate_path: str
    crate_name: str
    files: int
    code_lines: int
    app_code_lines: int
    test_code_lines: int
    functions: int
    structs: int
    traits: int
    unsafe_blocks: int
    test_to_app_ratio: float


@dataclass
class SinexMonthlyVelocity:
    month: str
    additions: int
    deletions: int
    commits: int
    files_changed: int


def iter_sinex_crate_metrics(path: Optional[Path] = None) -> Iterator[SinexCrateMetric]:
    """Yield per-crate metrics from sinex_structure_metrics.json → crates{}."""
    source = path or _derived_path("sinex_structure_metrics.json")
    if not source.exists():
        return
    with source.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for crate_key, crate_data in data.get("crates", {}).items():
        app = int(crate_data.get("app_code_lines", 0))
        test = int(crate_data.get("test_code_lines", 0))
        yield SinexCrateMetric(
            crate_path=crate_key,
            crate_name=crate_data.get("name", crate_key.rsplit("/", 1)[-1]),
            files=int(crate_data.get("files", 0)),
            code_lines=int(crate_data.get("code_lines", 0)),
            app_code_lines=app,
            test_code_lines=test,
            functions=int(crate_data.get("functions", 0)),
            structs=int(crate_data.get("structs", 0)),
            traits=int(crate_data.get("traits", 0)),
            unsafe_blocks=int(crate_data.get("unsafe_blocks", 0)),
            test_to_app_ratio=round(test / max(1, app), 3),
        )


def iter_sinex_monthly_velocity(path: Optional[Path] = None) -> Iterator[SinexMonthlyVelocity]:
    """Yield monthly velocity from sinex_temporal_metrics.json → monthly_velocity[]."""
    source = path or _derived_path("sinex_temporal_metrics.json")
    if not source.exists():
        return
    with source.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for entry in data.get("monthly_velocity", []):
        yield SinexMonthlyVelocity(
            month=entry.get("month", ""),
            additions=int(entry.get("lines", 0)),
            deletions=0,  # not tracked separately in temporal metrics
            commits=int(entry.get("commits", 0)),
            files_changed=int(entry.get("files_touched", 0)),
        )
