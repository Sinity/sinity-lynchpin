"""Shared manifest writing helper for ingest materializers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_manifest(path: Path, fields: dict[str, Any]) -> None:
    """Write a materializer manifest JSON file.

    Adds ``materialized_at`` (ISO timestamp) if not already present.
    Sorts keys for stable diffs.
    """
    if "materialized_at" not in fields:
        fields = {**fields, "materialized_at": datetime.now(timezone.utc).astimezone().isoformat()}
    path.write_text(json.dumps(fields, indent=2, sort_keys=True) + "\n", encoding="utf-8")
