from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

from ...core.cache import files_signature, persistent_cache
from ...core.config import get_config


@dataclass
class SessionSummaryRecord:
    summary_path: Path
    source_path: str
    provider: str
    title: str
    timeframe: Optional[str]
    summary: str
    generated_at: datetime
    highlights: List[str]
    decisions: List[str]
    follow_ups: List[str]
    action_items: List[dict[str, Any]]
    risks: List[str]
    raw_references: List[str]


@dataclass
class _SessionSummaryRow:
    summary_path: str
    source_path: str
    provider: str
    title: str
    timeframe: Optional[str]
    summary: str
    generated_at: datetime
    highlights_json: str
    decisions_json: str
    follow_ups_json: str
    action_items_json: str
    risks_json: str
    raw_references_json: str


def iter_session_summaries(root: Optional[Path] = None) -> Iterator[SessionSummaryRecord]:
    for row in _load_session_summaries(root):
        yield SessionSummaryRecord(
            summary_path=Path(row.summary_path),
            source_path=row.source_path,
            provider=row.provider,
            title=row.title,
            timeframe=row.timeframe,
            summary=row.summary,
            generated_at=row.generated_at,
            highlights=_decode_string_list(row.highlights_json),
            decisions=_decode_string_list(row.decisions_json),
            follow_ups=_decode_string_list(row.follow_ups_json),
            action_items=_decode_object_list(row.action_items_json),
            risks=_decode_string_list(row.risks_json),
            raw_references=_decode_string_list(row.raw_references_json),
        )


def iter_session_summaries_from(root: Path) -> Iterator[SessionSummaryRecord]:
    return iter_session_summaries(root)


@persistent_cache(
    "session_summary_records",
    depends_on=lambda root=None: files_signature(_summary_paths(_resolve_root(root))),
)
def _load_session_summaries(root: Optional[Path] = None) -> List[_SessionSummaryRow]:
    summary_root = _resolve_root(root)
    rows: List[_SessionSummaryRow] = []
    for path in _summary_paths(summary_root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        stat = path.stat()
        rows.append(
            _SessionSummaryRow(
                summary_path=str(path),
                source_path=str(payload.get("source_path") or ""),
                provider=_provider_from_source_path(payload.get("source_path")),
                title=str(payload.get("title") or ""),
                timeframe=_optional_text(payload.get("timeframe")),
                summary=str(payload.get("summary") or ""),
                generated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                highlights_json=json.dumps(_string_list(payload.get("highlights")), sort_keys=True),
                decisions_json=json.dumps(_string_list(payload.get("decisions")), sort_keys=True),
                follow_ups_json=json.dumps(_string_list(payload.get("follow_ups")), sort_keys=True),
                action_items_json=json.dumps(_object_list(payload.get("action_items")), sort_keys=True),
                risks_json=json.dumps(_string_list(payload.get("risks")), sort_keys=True),
                raw_references_json=json.dumps(_string_list(payload.get("raw_references")), sort_keys=True),
            )
        )
    rows.sort(key=lambda row: (row.generated_at, row.summary_path))
    return rows


def _resolve_root(root: Optional[Path]) -> Path:
    if root is not None:
        return Path(root)
    return get_config().session_summaries_dir


def _summary_paths(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.glob("*.json")
        if not path.name.startswith(".")
    )


def _provider_from_source_path(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.replace("\\", "/")
    marker = "/processed/markdown/"
    if marker not in normalized:
        return "unknown"
    tail = normalized.split(marker, 1)[1]
    provider = tail.split("/", 1)[0].strip()
    return provider or "unknown"


def _optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _object_list(value: object) -> List[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _decode_string_list(raw_json: str) -> List[str]:
    try:
        value = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    return _string_list(value)


def _decode_object_list(raw_json: str) -> List[dict[str, Any]]:
    try:
        value = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    return _object_list(value)
