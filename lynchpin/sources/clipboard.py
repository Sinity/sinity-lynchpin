"""Clipboard history source.

Reads the live Clipse history file and archived exports. Clipboard entries are
high-signal retrospective evidence, so values are preserved verbatim with light
metadata instead of being summarized at source level.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import blake2b
from pathlib import Path
from typing import Iterator, Optional

from ..core.cache import file_signature, write_text_if_changed
from ..core.config import get_config
from ..core.parse import as_local

__all__ = [
    "ClipboardEntry",
    "source_files",
    "entries",
    "entries_in_range",
]


@dataclass(frozen=True)
class ClipboardEntry:
    recorded_at: datetime
    value: str
    source: str
    file_path: str | None
    pinned: bool

    @property
    def date(self) -> date:
        return self.recorded_at.date()

    @property
    def kind(self) -> str:
        if self.file_path and self.file_path != "null":
            return "file"
        if self.value.startswith("http://") or self.value.startswith("https://"):
            return "url"
        if self.value.startswith("📷"):
            return "image"
        return "text"


def source_files() -> list[Path]:
    cfg = get_config()
    paths = [cfg.clipboard_live_file, *cfg.clipboard_export_files]
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        result.append(path)
    return result


def entries(*, paths: Optional[tuple[Path, ...]] = None) -> Iterator[ClipboardEntry]:
    seen: set[tuple[str, str]] = set()
    for path in paths or tuple(source_files()):
        for entry in _entries_from_file(path):
            key = (entry.recorded_at.isoformat(), entry.value)
            if key in seen:
                continue
            seen.add(key)
            yield entry


def entries_in_range(*, start: date, end: date, paths: Optional[tuple[Path, ...]] = None) -> list[ClipboardEntry]:
    seen: set[tuple[str, str]] = set()
    result: list[ClipboardEntry] = []
    for path in paths or tuple(source_files()):
        for entry in _entries_from_file_range(path, start=start, end=end):
            key = (entry.recorded_at.isoformat(), entry.value)
            if key in seen:
                continue
            seen.add(key)
            result.append(entry)
    result.sort(key=lambda entry: entry.recorded_at)
    return result


def _entries_from_file(path: Path) -> tuple[ClipboardEntry, ...]:
    return _entries_from_file_range(path, start=None, end=None)


def _entries_from_file_range(
    path: Path,
    *,
    start: date | None,
    end: date | None,
) -> tuple[ClipboardEntry, ...]:
    signature = file_signature(path)
    manifest = _read_entries_cache_manifest(path, signature)
    if manifest is None:
        entries = _parse_entries_from_file(path)
        manifest = _write_entries_cache(path, signature, entries)
    dates = manifest.get("dates")
    if not isinstance(dates, list):
        return ()
    wanted = _wanted_cache_dates(dates, start=start, end=end)
    result: list[ClipboardEntry] = []
    cache_dir = _entries_cache_dir(path)
    for day in wanted:
        result.extend(_read_entries_day_cache(cache_dir / f"{day}.json"))
    result.sort(key=lambda entry: entry.recorded_at)
    return tuple(result)


def _wanted_cache_dates(
    dates: list[object],
    *,
    start: date | None,
    end: date | None,
) -> list[str]:
    available = {str(day) for day in dates}
    if start is None or end is None:
        return sorted(available)
    result: list[str] = []
    current = start
    while current <= end:
        day = current.isoformat()
        if day in available:
            result.append(day)
        current += timedelta(days=1)
    return result


def _read_entries_cache_manifest(path: Path, signature: object) -> dict[str, object] | None:
    manifest_path = _entries_cache_manifest_path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if payload.get("signature") != _jsonable_signature(signature):
        return None
    dates = payload.get("dates")
    if not isinstance(dates, list):
        return None
    return dict(payload)


def _write_entries_cache(
    path: Path,
    signature: object,
    entries: tuple[ClipboardEntry, ...],
) -> dict[str, object]:
    cache_dir = _entries_cache_dir(path)
    by_day: dict[str, list[ClipboardEntry]] = defaultdict(list)
    for entry in entries:
        by_day[entry.date.isoformat()].append(entry)
    for day, day_entries in by_day.items():
        _write_entries_day_cache(cache_dir / f"{day}.json", tuple(day_entries))
    manifest = {
        "signature": _jsonable_signature(signature),
        "dates": sorted(by_day),
    }
    write_text_if_changed(
        _entries_cache_manifest_path(path),
        json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
    )
    return manifest


def _write_entries_day_cache(path: Path, entries: tuple[ClipboardEntry, ...]) -> None:
    write_text_if_changed(
        path,
        json.dumps([_entry_to_row(entry) for entry in entries], ensure_ascii=False) + "\n",
    )


def _read_entries_day_cache(path: Path) -> tuple[ClipboardEntry, ...]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ()
    if not isinstance(rows, list):
        return ()
    entries: list[ClipboardEntry] = []
    for row in rows:
        entry = _entry_from_row(row)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def _entry_to_row(entry: ClipboardEntry) -> dict[str, object]:
    return {
        "recorded_at": entry.recorded_at.isoformat(),
        "value": entry.value,
        "source": entry.source,
        "file_path": entry.file_path,
        "pinned": entry.pinned,
    }


def _entry_from_row(row: object) -> ClipboardEntry | None:
    if not isinstance(row, dict):
        return None
    try:
        return ClipboardEntry(
            recorded_at=as_local(datetime.fromisoformat(str(row["recorded_at"]))),
            value=str(row["value"]),
            source=str(row["source"]),
            file_path=None if row.get("file_path") is None else str(row["file_path"]),
            pinned=bool(row["pinned"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _entries_cache_dir(path: Path) -> Path:
    cache_dir = getattr(get_config(), "cache_dir", path.parent)
    digest = blake2b(str(path).encode("utf-8"), digest_size=12).hexdigest()
    return Path(cache_dir) / f"clipboard_entries_{digest}"


def _entries_cache_manifest_path(path: Path) -> Path:
    return _entries_cache_dir(path) / "manifest.json"


def _jsonable_signature(value: object) -> object:
    return json.loads(json.dumps(value, default=str))


def _parse_entries_from_file(path: Path) -> tuple[ClipboardEntry, ...]:
    if path.suffix.lower() == ".md":
        return tuple(_entries_from_markdown(path))
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ()
    rows = payload.get("clipboardHistory") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return ()
    result: list[ClipboardEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = _parse_recorded(row.get("recorded"))
        if stamp is None:
            continue
        value = str(row.get("value") or "")
        if not value:
            continue
        file_path = row.get("filePath")
        result.append(ClipboardEntry(
            recorded_at=stamp,
            value=value,
            source=str(path),
            file_path=None if file_path in (None, "", "null") else str(file_path),
            pinned=bool(row.get("pinned", False)),
        ))
    result.sort(key=lambda item: item.recorded_at)
    return tuple(result)


def _entries_from_markdown(path: Path) -> Iterator[ClipboardEntry]:
    text = path.read_text(encoding="utf-8", errors="replace")
    generated = None
    match = re.search(r"^generated:\s*([^\n]+)", text, flags=re.MULTILINE)
    if match:
        generated = _parse_recorded(match.group(1))
    stamp = generated or as_local(datetime.fromtimestamp(path.stat().st_mtime))
    for match in re.finditer(r"```(?:[a-zA-Z0-9_-]+)?\n(.*?)\n```", text, flags=re.DOTALL):
        value = match.group(1).strip()
        if value:
            yield ClipboardEntry(
                recorded_at=stamp,
                value=value,
                source=str(path),
                file_path=None,
                pinned=False,
            )


def _parse_recorded(value: object) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return as_local(datetime.fromisoformat(raw))
    except ValueError:
        return None
