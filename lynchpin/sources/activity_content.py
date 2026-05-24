"""Canonical ActivityWatch content rollups joined to title metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator

from ..core.config import get_config

__all__ = [
    "ActivityContentDay",
    "ActivityTitleUsage",
    "activity_content_daily_path",
    "activity_content_manifest_path",
    "activity_title_usage_path",
    "iter_activity_content_days",
    "iter_activity_title_usage",
]


@dataclass(frozen=True)
class ActivityContentDay:
    date: date
    focused_seconds: float
    matched_seconds: float
    gpt_matched_seconds: float
    unmatched_seconds: float
    matched_ratio: float
    gpt_matched_ratio: float
    activity_seconds: dict[str, float]
    content_type_seconds: dict[str, float]
    attention_seconds: dict[str, float]
    topic_seconds: dict[str, float]
    platform_seconds: dict[str, float]
    source_counts: dict[str, int]


@dataclass(frozen=True)
class ActivityTitleUsage:
    title_hash: str
    app: str
    normalized_title: str
    example_title: str
    focused_seconds: float
    span_count: int
    first_date: date | None
    last_date: date | None
    matched: bool
    classification_source: str | None = None
    confidence: float | None = None
    activity: str | None = None
    content_type: str | None = None
    attention_level: str | None = None
    topic_category: str | None = None
    platform: str | None = None


def activity_content_daily_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "activity_content/daily.ndjson"


def activity_content_manifest_path(root: Path | None = None) -> Path:
    return activity_content_daily_path(root).with_suffix(".manifest.json")


def activity_title_usage_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "activity_content/title_usage.ndjson"


def iter_activity_content_days(path: Path | None = None) -> Iterator[ActivityContentDay]:
    target = path or activity_content_daily_path()
    if not target.exists():
        raise FileNotFoundError(
            f"canonical ActivityWatch content materialization is missing: {target}. "
            "Run python -m lynchpin.cli.materialize --all."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                yield ActivityContentDay(
                    date=date.fromisoformat(str(payload["date"])),
                    focused_seconds=float(payload.get("focused_seconds") or 0.0),
                    matched_seconds=float(payload.get("matched_seconds") or 0.0),
                    gpt_matched_seconds=float(payload.get("gpt_matched_seconds") or 0.0),
                    unmatched_seconds=float(payload.get("unmatched_seconds") or 0.0),
                    matched_ratio=float(payload.get("matched_ratio") or 0.0),
                    gpt_matched_ratio=float(payload.get("gpt_matched_ratio") or 0.0),
                    activity_seconds=_float_map(payload.get("activity_seconds")),
                    content_type_seconds=_float_map(payload.get("content_type_seconds")),
                    attention_seconds=_float_map(payload.get("attention_seconds")),
                    topic_seconds=_float_map(payload.get("topic_seconds")),
                    platform_seconds=_float_map(payload.get("platform_seconds")),
                    source_counts=_int_map(payload.get("source_counts")),
                )


def iter_activity_title_usage(path: Path | None = None) -> Iterator[ActivityTitleUsage]:
    target = path or activity_title_usage_path()
    if not target.exists():
        raise FileNotFoundError(
            f"canonical ActivityWatch title-usage materialization is missing: {target}. "
            "Run python -m lynchpin.cli.materialize --all."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            first = payload.get("first_date")
            last = payload.get("last_date")
            yield ActivityTitleUsage(
                title_hash=str(payload.get("title_hash") or ""),
                app=str(payload.get("app") or ""),
                normalized_title=str(payload.get("normalized_title") or ""),
                example_title=str(payload.get("example_title") or ""),
                focused_seconds=float(payload.get("focused_seconds") or 0.0),
                span_count=int(payload.get("span_count") or 0),
                first_date=date.fromisoformat(str(first)) if first else None,
                last_date=date.fromisoformat(str(last)) if last else None,
                matched=bool(payload.get("matched")),
                classification_source=_str_or_none(payload.get("classification_source")),
                confidence=_float_or_none(payload.get("confidence")),
                activity=_str_or_none(payload.get("activity")),
                content_type=_str_or_none(payload.get("content_type")),
                attention_level=_str_or_none(payload.get("attention_level")),
                topic_category=_str_or_none(payload.get("topic_category")),
                platform=_str_or_none(payload.get("platform")),
            )


def _float_map(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float(raw or 0.0) for key, raw in value.items()}


def _int_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(raw or 0) for key, raw in value.items()}


def _str_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
