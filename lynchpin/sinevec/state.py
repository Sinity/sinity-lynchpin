from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from ..core.config import get_config
from . import embed_utils


@dataclass(frozen=True)
class TokenUsage:
    path: str
    tokens: int


@dataclass(frozen=True)
class EmbeddingState:
    version: Optional[int]
    created_at: Optional[datetime]
    token_total: int
    token_usage: List[TokenUsage]
    source_file: Path


def load_embedding_state() -> Optional[EmbeddingState]:
    candidates: List[EmbeddingState] = []
    for root in _iter_candidate_dirs():
        for path in _iter_state_files(root):
            state = _parse_state(path)
            if state:
                candidates.append(state)

    if not candidates:
        return None

    with_usage = [state for state in candidates if state.token_usage]
    if with_usage:
        return max(with_usage, key=_state_sort_key)
    return max(candidates, key=_state_sort_key)


def _iter_candidate_dirs() -> Iterable[Path]:
    cfg = get_config()
    yield cfg.sinevec_state_dir
    yield embed_utils.STATE_DIR


def _iter_state_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(root.glob("*state*.json"))


def _state_sort_key(state: EmbeddingState) -> tuple[int, float]:
    try:
        mtime = state.source_file.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (state.version or 0, mtime)


def _parse_state(path: Path) -> Optional[EmbeddingState]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    version_raw = payload.get("version")
    version = _to_int(version_raw)

    created = _parse_datetime(payload.get("created_at") or payload.get("last_updated"))

    token_usage: List[TokenUsage] = []
    token_total = 0

    usage = payload.get("token_usage")
    if isinstance(usage, dict):
        total_override = _to_int(usage.get("total"))
        for key, value in usage.items():
            if key == "total":
                continue
            tokens = _to_int(value)
            if tokens is None:
                continue
            token_usage.append(TokenUsage(path=str(key), tokens=tokens))
            token_total += tokens
        if total_override is not None:
            token_total = total_override
    else:
        token_total = _to_int(payload.get("total_tokens")) or _to_int(usage) or 0

    return EmbeddingState(
        version=version,
        created_at=created,
        token_total=token_total,
        token_usage=token_usage,
        source_file=path,
    )


def _parse_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _to_int(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None
