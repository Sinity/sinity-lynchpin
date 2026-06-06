"""Canonical derived temporal signal events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config


@dataclass(frozen=True)
class TemporalSignalEvent:
    kind: str
    signal: str
    event_date: date
    summary: str
    payload: dict[str, Any]


def temporal_signals_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "temporal/signals.ndjson"


def temporal_signals_manifest_path(root: Path | None = None) -> Path:
    return temporal_signals_path(root).with_suffix(".manifest.json")


def iter_temporal_signals(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[TemporalSignalEvent]:
    target = path or temporal_signals_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("temporal_signals", window=(start, end) if start is not None and end is not None else None)
    if not target.exists():
        raise FileNotFoundError(
            f"canonical temporal signal product is missing: {target}. "
            "Run python -m lynchpin.ingest.temporal_signals_materialize."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            event_date = date.fromisoformat(str(payload["event_date"]))
            if start is not None and event_date < start:
                continue
            if end is not None and event_date >= end:
                continue
            event_payload = payload.get("payload")
            yield TemporalSignalEvent(
                kind=str(payload.get("kind") or ""),
                signal=str(payload.get("signal") or ""),
                event_date=event_date,
                summary=str(payload.get("summary") or ""),
                payload=event_payload if isinstance(event_payload, dict) else {},
            )
