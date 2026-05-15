"""Shared work-event kind label contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

KindSource = Literal["polylogue", "lynchpin_overlay", "agreement", "disagreement"]
ConfidenceTier = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class WorkEventKindLabel:
    """A single classification of a Polylogue work-event by either side."""

    kind: str
    confidence: float
    source: KindSource
    tier: ConfidenceTier
    polylogue_kind: str | None
    polylogue_confidence: float
    overlay_kind: str | None
    overlay_confidence: float
    features: dict[str, Any]


__all__ = [
    "ConfidenceTier",
    "KindSource",
    "WorkEventKindLabel",
]
