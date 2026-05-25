"""Shared work-event kind label contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

KindSource = Literal["source", "lynchpin_overlay", "agreement", "disagreement"]
ConfidenceTier = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class WorkEventKindLabel:
    """Resolved label for a work-event plus its source and overlay evidence."""

    kind: str
    confidence: float
    source: KindSource
    tier: ConfidenceTier
    source_kind: str | None
    source_confidence: float
    overlay_kind: str | None
    overlay_confidence: float
    features: dict[str, Any]


__all__ = [
    "ConfidenceTier",
    "KindSource",
    "WorkEventKindLabel",
]
