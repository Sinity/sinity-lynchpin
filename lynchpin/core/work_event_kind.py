"""Shared work-event kind label contract.

Data-model half: the ``WorkEventKindLabel`` DTO plus the ``KindSource`` /
``ConfidenceTier`` type aliases. This is the pure type contract with no
classification logic. The re-classifier that produces these labels (the
"graph" half) lives in ``lynchpin/graph/work_event_kind.py``.
"""

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
