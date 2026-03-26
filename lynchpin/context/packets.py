"""Context packet entry point built on shared evidence-window summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .packet_builders import build_current_state


def build_recent_state(
    *,
    days: int = 14,
    end: Optional[datetime] = None,
) -> dict[str, object]:
    """Build a standard-tier context packet for LLM consumption."""
    return build_current_state(days=days, end=end, tier="standard")
