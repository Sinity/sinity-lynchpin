"""Source module for SPARK review LLM-produced artifacts.

These artifacts were produced by LLM review passes and cannot be
regenerated deterministically. They are preserved as historical datasets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from ...core.config import get_config

_DERIVED_DIR = "artefacts/analysis/derived"


def _derived_path(subpath: str) -> Path:
    cfg = get_config()
    return cfg.repo_root / _DERIVED_DIR / subpath


@dataclass
class SparkReviewResult:
    packet_id: str
    ecosystem: str
    period: str
    coverage_pct: float
    quality_score: Optional[float]
    review_text: str
    model: str
    reviewed_at: str


def iter_spark_reviews() -> Iterator[SparkReviewResult]:
    """Read SPARK review result files from artefacts/analysis/derived/spark_review_results/."""
    results_dir = _derived_path("spark_review_results")
    if not results_dir.is_dir():
        return
    for fp in sorted(results_dir.glob("*.json")):
        try:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        packet_id = data.get("packet_id", fp.stem)
        ecosystem = data.get("ecosystem", "")
        period = data.get("period", "")
        model = data.get("model", "")
        reviewed_at = data.get("reviewed_at", "")
        review_text = json.dumps(data.get("units", []))
        # Extract quality_score from summary if present
        quality_score = None
        if isinstance(data.get("quality_score"), (int, float)):
            quality_score = float(data["quality_score"])
        yield SparkReviewResult(
            packet_id=packet_id,
            ecosystem=ecosystem,
            period=period,
            coverage_pct=0.0,  # filled from reduction if available
            quality_score=quality_score,
            review_text=review_text,
            model=model,
            reviewed_at=reviewed_at,
        )


@dataclass
class SparkReviewReduction:
    """Summary row from the spark_review_reduction.json file."""

    coverage_pct: float
    reviewed_packet_count: int
    expected_packet_count: int
    total_units: int
    abstention_rate: float


def latest_reduction() -> Optional[SparkReviewReduction]:
    """Read the latest SPARK review reduction summary."""
    reduction_path = _derived_path("spark_review_reduction.json")
    if not reduction_path.exists():
        return None
    try:
        with reduction_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    summary = data.get("summary", {})
    return SparkReviewReduction(
        coverage_pct=float(summary.get("coverage_pct", 0)),
        reviewed_packet_count=int(summary.get("reviewed_packet_count", 0)),
        expected_packet_count=int(summary.get("expected_packet_count", 0)),
        total_units=int(summary.get("total_units", 0)),
        abstention_rate=float(summary.get("abstention_rate", 0)),
    )
