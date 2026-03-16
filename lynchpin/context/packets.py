"""Context packet entry point — delegates to packet_builders.

Maintains the ``build_recent_state()`` API for backward compatibility
while composing from the typed packet builder system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .packet_builders import build_current_state


def build_recent_state(
    *,
    days: int = 14,
    end: Optional[datetime] = None,
) -> dict[str, object]:
    """Build a compact context packet for LLM consumption.

    This is the original entry point. It now delegates to
    ``packet_builders.build_current_state`` with "standard" tier.
    """
    return build_current_state(days=days, end=end, tier="standard")
