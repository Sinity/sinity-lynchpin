"""Tolerant parser for Nix ``--log-format internal-json`` captures."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.parse import parse_datetime


@dataclass(frozen=True)
class NixInternalJsonPhase:
    activity_id: str
    parent_id: str | None
    name: str | None
    activity_type: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    result_type_counts: dict[str, int]
    message_count: int
    status: str
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NixInternalJsonSummary:
    path: str
    exists: bool
    line_count: int
    parsed_count: int
    malformed_count: int
    activity_count: int
    activity_type_counts: dict[str, int]
    result_type_counts: dict[str, int]
    message_level_counts: dict[str, int]
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    phase_count: int
    phases: tuple[NixInternalJsonPhase, ...]
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_internal_json(path: str | Path | None) -> NixInternalJsonSummary:
    """Summarize a Nix internal-json NDJSON capture without assuming one schema.

    Nix's internal log stream is deliberately low-level. Different versions and
    call sites expose activity ids, result types, timestamps, and messages under
    slightly different keys. This parser preserves counts and timing when those
    fields exist, and emits caveats instead of rejecting the whole capture when
    optional fields are absent.
    """
    if path is None:
        return _missing("<missing>", "manifest did not name an internal-json capture path")
    p = Path(path)
    if not p.exists():
        return _missing(str(p), "internal-json capture path does not exist")
    line_count = 0
    parsed_count = 0
    malformed_count = 0
    activity_ids: set[str] = set()
    activity_types: Counter[str] = Counter()
    result_types: Counter[str] = Counter()
    message_levels: Counter[str] = Counter()
    timestamps: list[datetime] = []
    phase_builders: dict[str, dict[str, Any]] = {}
    with p.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            line_count += 1
            if text.startswith("@nix "):
                text = text.removeprefix("@nix ").lstrip()
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                malformed_count += 1
                continue
            if not isinstance(row, dict):
                malformed_count += 1
                continue
            parsed_count += 1
            activity_id = _first_text(row, "id", "activity", "activity_id", "act", "parent")
            if activity_id is not None:
                activity_ids.add(activity_id)
            action = _first_text(row, "action", "type", "kind")
            if action is not None:
                result_types[action] += 1
            activity_type = _first_text(row, "activity_type", "type", "name")
            if activity_type is not None:
                activity_types[activity_type] += 1
            level = _first_text(row, "level", "lvl", "severity")
            if level is not None:
                message_levels[level] += 1
            timestamp = _timestamp(row)
            if timestamp is not None:
                timestamps.append(timestamp)
            _accumulate_phase(phase_builders, row=row, timestamp=timestamp)
    caveats = []
    if malformed_count:
        caveats.append(f"{malformed_count} malformed internal-json lines skipped")
    if not timestamps:
        caveats.append("internal-json capture has no parseable timestamps")
    if not activity_ids:
        caveats.append("internal-json capture has no activity identifiers")
    phases = tuple(_finalize_phases(phase_builders))
    if parsed_count and not phases:
        caveats.append("internal-json capture has no reconstructable activity phases")
    return NixInternalJsonSummary(
        path=str(p),
        exists=True,
        line_count=line_count,
        parsed_count=parsed_count,
        malformed_count=malformed_count,
        activity_count=len(activity_ids),
        activity_type_counts=dict(sorted(activity_types.items())),
        result_type_counts=dict(sorted(result_types.items())),
        message_level_counts=dict(sorted(message_levels.items())),
        first_timestamp=min(timestamps) if timestamps else None,
        last_timestamp=max(timestamps) if timestamps else None,
        phase_count=len(phases),
        phases=phases,
        caveats=tuple(caveats),
    )


def _missing(path: str, reason: str) -> NixInternalJsonSummary:
    return NixInternalJsonSummary(
        path=path,
        exists=False,
        line_count=0,
        parsed_count=0,
        malformed_count=0,
        activity_count=0,
        activity_type_counts={},
        result_type_counts={},
        message_level_counts={},
        first_timestamp=None,
        last_timestamp=None,
        phase_count=0,
        phases=(),
        caveats=(reason,),
    )


def _accumulate_phase(
    builders: dict[str, dict[str, Any]],
    *,
    row: dict[str, Any],
    timestamp: datetime | None,
) -> None:
    activity_id = _first_text(row, "id", "activity", "activity_id", "act")
    if activity_id is None:
        return
    builder = builders.setdefault(
        activity_id,
        {
            "activity_id": activity_id,
            "parent_id": None,
            "name": None,
            "activity_type": None,
            "started_at": None,
            "ended_at": None,
            "result_type_counts": Counter(),
            "message_count": 0,
            "saw_start": False,
            "saw_stop": False,
        },
    )
    parent_id = _first_text(row, "parent", "parent_id")
    if parent_id is not None:
        builder["parent_id"] = parent_id
    name = _first_text(row, "name", "text", "message", "msg")
    if name is not None and builder["name"] is None:
        builder["name"] = name
    activity_type = _first_text(row, "activity_type", "type", "kind")
    if activity_type is not None and builder["activity_type"] is None:
        builder["activity_type"] = activity_type
    action = (_first_text(row, "action", "type", "kind") or "").lower()
    if action in {"start", "startactivity", "activity_start"}:
        builder["saw_start"] = True
        if timestamp is not None:
            builder["started_at"] = _min_dt(builder["started_at"], timestamp)
    elif action in {"stop", "stopactivity", "activity_stop", "finish", "finished"}:
        builder["saw_stop"] = True
        if timestamp is not None:
            builder["ended_at"] = _max_dt(builder["ended_at"], timestamp)
    elif action in {"result", "msg", "message"}:
        result_type = _first_text(row, "result_type", "type", "kind", "level") or action
        builder["result_type_counts"][result_type] += 1
        if action in {"msg", "message"}:
            builder["message_count"] += 1
    elif timestamp is not None:
        builder["started_at"] = _min_dt(builder["started_at"], timestamp)
        builder["ended_at"] = _max_dt(builder["ended_at"], timestamp)


def _finalize_phases(builders: dict[str, dict[str, Any]]) -> list[NixInternalJsonPhase]:
    phases = []
    for activity_id, builder in builders.items():
        started_at = builder["started_at"]
        ended_at = builder["ended_at"]
        duration = None
        caveats = []
        if started_at is None:
            caveats.append("missing start timestamp")
        if ended_at is None:
            caveats.append("missing end timestamp")
        if started_at is not None and ended_at is not None:
            if ended_at >= started_at:
                duration = round((ended_at - started_at).total_seconds(), 6)
            else:
                caveats.append("end timestamp precedes start timestamp")
        if not builder["saw_start"]:
            caveats.append("no explicit start action")
        if not builder["saw_stop"]:
            caveats.append("no explicit stop action")
        phases.append(
            NixInternalJsonPhase(
                activity_id=activity_id,
                parent_id=builder["parent_id"],
                name=builder["name"],
                activity_type=builder["activity_type"],
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=duration,
                result_type_counts=dict(sorted(builder["result_type_counts"].items())),
                message_count=int(builder["message_count"]),
                status="complete" if duration is not None and not caveats else "partial",
                caveats=tuple(caveats),
            )
        )
    phases.sort(key=lambda row: (row.started_at or datetime.max.replace(tzinfo=timezone.utc), row.activity_id))
    return phases


def _min_dt(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right < left else left


def _max_dt(left: datetime | None, right: datetime) -> datetime:
    return right if left is None or right > left else left


def _first_text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "time", "ts", "at"):
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
        parsed = parse_datetime(str(value))
        if parsed is not None:
            return parsed
    return None


__all__ = ["NixInternalJsonPhase", "NixInternalJsonSummary", "summarize_internal_json"]
