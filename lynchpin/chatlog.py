from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

import yaml

from .config import get_config

_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[Tt](\d{2})-(\d{2})-(\d{2})")


@dataclass
class ChatTranscript:
    provider: str
    slug: str
    title: str
    path: Path
    started_at: datetime
    tokens: Optional[int]
    words: Optional[int]
    attachment_count: int
    attachment_bytes: Optional[int]

    def to_dict(self) -> Dict[str, object]:
        return {
            "provider": self.provider,
            "slug": self.slug,
            "title": self.title,
            "path": str(self.path),
            "started_at": self.started_at.isoformat(),
            "tokens": self.tokens,
            "words": self.words,
            "attachment_count": self.attachment_count,
            "attachment_bytes": self.attachment_bytes,
        }


def iter_transcripts(
    providers: Optional[Sequence[str]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Iterator[ChatTranscript]:
    cfg = get_config()
    root = cfg.polylogue_root
    if not root.exists():
        return iter(())

    def generator() -> Iterator[ChatTranscript]:
        provider_dirs: List[Path]
        if providers:
            provider_dirs = [root / name for name in providers]
        else:
            provider_dirs = [child for child in root.iterdir() if child.is_dir()]

        for provider_dir in provider_dirs:
            if not provider_dir.exists() or not provider_dir.is_dir():
                continue
            provider_name = provider_dir.name
            for convo_file in provider_dir.rglob("conversation.md"):
                transcript = _build_transcript(provider_name, convo_file)
                if not transcript:
                    continue
                if start and transcript.started_at < start:
                    continue
                if end and transcript.started_at >= end:
                    continue
                yield transcript

    return generator()


def transcripts_by_date(target: date, provider: Optional[str] = None) -> List[ChatTranscript]:
    day_start = datetime.combine(target, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    providers = [provider] if provider else None
    return list(iter_transcripts(providers=providers, start=day_start, end=day_end))


def _build_transcript(provider: str, convo_path: Path) -> Optional[ChatTranscript]:
    metadata = _read_front_matter(convo_path)
    slug = _slug_from_metadata(metadata) or convo_path.parent.name
    title = metadata.get("title") or slug
    started_at = _infer_timestamp(metadata, slug) or datetime.fromtimestamp(convo_path.stat().st_mtime)
    tokens = _coerce_int(
        metadata.get("totalTokensApprox")
        or metadata.get("inputTokensApprox")
        or metadata.get("outputTokensApprox")
    )
    words = _coerce_int(metadata.get("totalWordsApprox"))
    attachment_count = _coerce_int(metadata.get("attachmentCount"), default=0)
    attachment_bytes = _coerce_int(metadata.get("attachmentBytes"))
    return ChatTranscript(
        provider=provider,
        slug=slug,
        title=title,
        path=convo_path,
        started_at=started_at,
        tokens=tokens,
        words=words,
        attachment_count=attachment_count or 0,
        attachment_bytes=attachment_bytes,
    )


def _read_front_matter(path: Path) -> Dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    block: List[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        block.append(line)
    else:
        return {}
    raw = "\n".join(block)
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        parsed = {}
    return parsed


def _slug_from_metadata(metadata: Dict[str, object]) -> Optional[str]:
    polylogue_meta = metadata.get("polylogue") if isinstance(metadata.get("polylogue"), dict) else None
    if isinstance(polylogue_meta, dict):
        slug = polylogue_meta.get("slug") or polylogue_meta.get("title")
        if isinstance(slug, str) and slug.strip():
            return slug.strip()
    slug_value = metadata.get("slug") or metadata.get("title")
    if isinstance(slug_value, str) and slug_value.strip():
        return slug_value.strip()
    return None


def _infer_timestamp(metadata: Dict[str, object], slug: Optional[str]) -> Optional[datetime]:
    candidates = [
        metadata.get("sessionPath"),
        metadata.get("sourceId"),
        metadata.get("polylogue", {}).get("sessionPath") if isinstance(metadata.get("polylogue"), dict) else None,
        slug,
    ]
    for value in candidates:
        if not isinstance(value, str):
            continue
        match = _TIME_RE.search(value)
        if not match:
            continue
        iso = f"{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}"
        try:
            return datetime.fromisoformat(iso)
        except ValueError:
            continue
    return None


def _coerce_int(value: object, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

