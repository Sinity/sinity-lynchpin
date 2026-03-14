"""Command density, chat token density, and productivity metrics."""

from __future__ import annotations

from collections import Counter
from typing import Dict, Optional, Sequence


def categorise_command(cwd: Optional[str], command: str) -> str:
    """Categorise a shell command by its working directory."""
    if not cwd or not isinstance(cwd, str):
        return "misc"
    path = cwd.strip()
    lowered = path.lower()
    if "project/sinex" in lowered or lowered.rstrip("/").endswith("sinex"):
        return "development:sinex"
    if "sinnix" in lowered:
        return "infrastructure:sinnix"
    if "/realm/project/" in lowered:
        return "development:other"
    if lowered.startswith("/realm/home") or lowered.startswith("/home"):
        return "home"
    return "misc"


def commands_by_category(commands: Sequence) -> Dict[str, int]:
    """Bucket shell commands into categories by cwd."""
    bucket: Counter = Counter()
    for command in commands:
        cwd = getattr(command, "cwd", None)
        cmd = getattr(command, "command", "")
        category = categorise_command(cwd, cmd)
        bucket[category] += 1
    return dict(sorted(bucket.items()))


def command_density(commands: Sequence, active_hours: float) -> float:
    """Commands per active hour."""
    if active_hours <= 0:
        return 0.0
    return len(commands) / active_hours


def chat_token_density(transcripts: Sequence, active_hours: float) -> float:
    """Chat tokens per active hour."""
    if active_hours <= 0:
        return 0.0
    total_tokens = sum(getattr(t, "tokens", 0) or 0 for t in transcripts)
    return total_tokens / active_hours
