from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence

from ...core.cache import file_digest, persistent_cache
from ...core.config import get_config
from .webhistory_common import WEBHISTORY_TIMESTAMP_FIELDS, parse_webhistory_timestamp

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)


TS_FIELDS = WEBHISTORY_TIMESTAMP_FIELDS
_RAW_SUFFIX_PRIORITY = {
    ".jsonl": 0,
    ".ndjson": 0,
    ".json": 1,
    ".csv": 2,
}


@dataclass(frozen=True)
class WebHistoryRawEntry:
    timestamp: datetime
    url: str
    title: str
    payload_json: str
    source_file: str

    def payload(self) -> dict[str, object]:
        return json.loads(self.payload_json)


def raw_files(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> List[Path]:
    cfg = get_config()
    base = root or cfg.webhistory_raw_dir
    if files:
        paths: List[Path] = []
        for file in files:
            candidate = Path(file)
            if not candidate.is_absolute():
                candidate = base / candidate
            paths.append(candidate)
        return paths
    if not base.exists():
        return []
    candidates = []
    for path in base.iterdir():
        if not path.is_file():
            continue
        suffixes = {suffix.lower() for suffix in path.suffixes}
        if ".pre_dedup" in suffixes:
            continue
        if not suffixes.intersection({".csv", ".json", ".ndjson", ".jsonl"}):
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda p: (p.stem, _RAW_SUFFIX_PRIORITY.get(p.suffix.lower(), 99), p.name))


@persistent_cache("webhistory_raw_file", depends_on=lambda path, signature: signature)
def _load_raw_file(
    path: Path,
    _signature: tuple[str, int | None, int | None, str | None],
) -> List[WebHistoryRawEntry]:
    entries: List[WebHistoryRawEntry] = []
    suffix = path.suffix.lower()
    if suffix in {".json", ".ndjson", ".jsonl"}:
        entries.extend(_load_raw_json(path))
    elif suffix == ".csv":
        entries.extend(_load_raw_csv(path))
    else:
        raise ValueError(f"Unsupported webhistory file: {path}")
    return entries


def iter_entries(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> Iterator[WebHistoryRawEntry]:
    for path in raw_files(root, files):
        for entry in load_raw_file(path):
            yield entry


def iter_file_entries(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> Iterator[tuple[Path, List[WebHistoryRawEntry]]]:
    for path in raw_files(root, files):
        yield path, load_raw_file(path)


def load_raw_file(
    path: Path,
    signature: Optional[tuple[str, int | None, int | None, str | None]] = None,
) -> List[WebHistoryRawEntry]:
    if signature is None:
        signature = file_digest(path)
    return _load_raw_file(path, signature)


def _load_raw_json(path: Path) -> Iterable[WebHistoryRawEntry]:
    suffix = path.suffix.lower()
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        first_nonempty: str | None = None
        for line in fh:
            raw = line.strip()
            if raw:
                first_nonempty = raw
                break
        if first_nonempty is None:
            return []

        if suffix in {".ndjson", ".jsonl"}:
            entries = list(_entries_from_lines((first_nonempty,), path))
            entries.extend(_entries_from_lines(fh, path))
            return entries

        if first_nonempty.startswith("[") or first_nonempty.startswith("{"):
            fh.seek(0)
            try:
                payload = json.load(fh)
            except json.JSONDecodeError:
                entries = list(_entries_from_lines((first_nonempty,), path))
                entries.extend(_entries_from_lines(fh, path))
                return entries
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                return []
            return list(_entries_from_objects(payload, path))

        entries = list(_entries_from_lines((first_nonempty,), path))
        entries.extend(_entries_from_lines(fh, path))
        return entries


def _load_raw_csv(path: Path) -> Iterable[WebHistoryRawEntry]:
    entries: List[WebHistoryRawEntry] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row:
                continue
            dt = None
            if row.get("DateTime"):
                dt = _parse_ts(row["DateTime"])
            if not dt and row.get("date") and row.get("time"):
                dt = _parse_ts(f"{row['date']} {row['time']}")
            if not dt:
                for field in TS_FIELDS:
                    key = field.lower()
                    if key in row and row[key]:
                        dt = _parse_ts(row[key])
                        if dt:
                            break
            if not dt:
                continue
            url = (
                row.get("url")
                or row.get("navigatedtourl")
                or row.get("NavigatedToUrl")
                or ""
            )
            title = (
                row.get("title")
                or row.get("pagetitle")
                or row.get("PageTitle")
                or ""
            )
            payload = dict(row)
            entries.append(_make_entry(dt, url, title, payload, path))
    return entries


def _entries_from_objects(objs: Iterable[object], path: Path) -> Iterator[WebHistoryRawEntry]:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        entry = _entry_from_payload(obj, path)
        if entry:
            yield entry


def _entries_from_lines(lines: Iterable[str], path: Path) -> Iterator[WebHistoryRawEntry]:
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        entry = _entry_from_payload(obj, path)
        if entry:
            yield entry


def _entry_from_payload(payload: dict[str, object], path: Path) -> WebHistoryRawEntry | None:
    dt = None
    for field in TS_FIELDS:
        if field in payload and payload[field] not in (None, ""):
            dt = _parse_ts(payload[field])
            if dt:
                break
    if not dt:
        return None
    url = payload.get("url") if isinstance(payload.get("url"), str) else ""
    title = payload.get("title") if isinstance(payload.get("title"), str) else ""
    return _make_entry(dt, url, title, payload, path)


def _make_entry(
    dt: datetime, url: str, title: str, payload: dict[str, object], path: Path
) -> WebHistoryRawEntry:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    payload_json = json.dumps(payload, ensure_ascii=False)
    return WebHistoryRawEntry(
        timestamp=dt.astimezone(timezone.utc),
        url=url or "",
        title=title or "",
        payload_json=payload_json,
        source_file=str(path),
    )


def _parse_ts(value: object) -> Optional[datetime]:
    return parse_webhistory_timestamp(value)
