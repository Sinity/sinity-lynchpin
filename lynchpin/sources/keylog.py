"""Keylog source.

Key events, text-shape metadata, and optional snapshot text are exposed through
separate APIs so callers can choose the product they actually need.
"""

from __future__ import annotations

from functools import lru_cache
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import get_config
from ..core.coverage import CoverageBounds
from ..core.parse import as_local, iter_dates, parse_datetime
from ..core.primitives import date_to_dt_range
from ..core.primitives import logical_date
from ..core.source import read_jsonl_with

__all__ = [
    "KeylogEvent",
    "KeylogDayActivity",
    "KeylogTextSnapshot",
    "log_files",
    "events",
    "text_snapshots",
    "keypresses",
    "keypress_timestamps",
    "keypress_count",
    "has_coverage",
    "daily_activity",
    "coverage_bounds",
    "KeylogInputTrace",
    "KeylogFileRead",
    "read_input_trace",
]


@dataclass(frozen=True)
class KeylogEvent:
    ts: datetime
    event: str
    session: str | None
    window: str | None
    keycode: str | None
    changed: bool | None
    modifiers: tuple[str, ...] = ()
    code: str | None = None
    value: int | None = None
    modifier_state_known: bool = False
    has_clipboard: bool = False
    source_path: str | None = None
    source_line: int | None = None


@dataclass(frozen=True)
class _KeylogAnalysisRecord:
    ts: datetime
    event: str
    keycode: str | None
    changed: bool | None
    modifiers: tuple[str, ...]
    text: str | None


@dataclass(frozen=True)
class KeylogDayActivity:
    date: date
    event_count: int
    keypress_count: int
    changed_keypress_count: int
    session_count: int
    first_ts: datetime | None
    last_ts: datetime | None


@dataclass(frozen=True)
class KeylogTextSnapshot:
    ts: datetime
    event: str
    session: str | None
    window: str | None
    text: str


def _logs_root() -> Path:
    return get_config().keylog_root / "logs"


def _ensure_keylog_materialized(*, start: date | None = None, end: date | None = None) -> None:
    from ..materialization import ensure_materialized

    window = (start, end + timedelta(days=1)) if start is not None and end is not None else None
    ensure_materialized("keylog", cfg=get_config(), window=window)


def _date_from_name(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


@lru_cache(maxsize=8)
def _indexed_log_files(root: str, signature: tuple[int, int] | None) -> tuple[tuple[date, Path], ...]:
    _ = signature
    path = Path(root)
    if not path.exists():
        return ()
    rows = []
    for item in path.glob("*.jsonl"):
        d = _date_from_name(item)
        if d is not None:
            rows.append((d, item))
    return tuple(sorted(rows, key=lambda row: row[0]))


def _log_dir_signature(root: Path) -> tuple[int, int] | None:
    try:
        stat = root.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, sum(1 for _ in root.glob("*.jsonl"))


def log_files(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    ensure: bool = True,
) -> list[Path]:
    if ensure:
        _ensure_keylog_materialized(start=start, end=end)
    root = _logs_root()
    files = []
    for d, path in _indexed_log_files(str(root), _log_dir_signature(root)):
        if start and d < start:
            continue
        if end and d > end:
            continue
        files.append(path)
    return sorted(files)


def _candidate_files(start: datetime, end: datetime, *, ensure: bool = True) -> list[Path]:
    # Files are named by the UTC log date, while callers usually pass local
    # datetimes. Pad by one day so midnight-adjacent local intervals still see
    # the corresponding UTC file.
    s = as_local(start).date() - timedelta(days=1)
    e = as_local(end).date() + timedelta(days=1)
    return log_files(start=s, end=e, ensure=ensure)


def events(
    *,
    start: datetime,
    end: datetime,
    kinds: Optional[set[str]] = None,
    ensure: bool = True,
) -> Iterator[KeylogEvent]:
    start_local = as_local(start)
    end_local = as_local(end)
    def _hydrate(rec: dict[str, Any]) -> KeylogEvent | None:
        kind = str(rec.get("event") or "")
        if kinds is not None and kind not in kinds:
            return None
        ts = parse_datetime(rec.get("ts"))
        if ts is None:
            return None
        ts_local = as_local(ts)
        if ts_local < start_local or ts_local >= end_local:
            return None
        return KeylogEvent(
            ts=ts_local,
            event=kind,
            session=rec.get("session"),
            window=rec.get("window"),
            keycode=rec.get("keycode"),
            changed=rec.get("changed") if isinstance(rec.get("changed"), bool) else None,
            modifiers=_modifier_state(rec),
            code=rec.get("code"),
            value=rec.get("value") if type(rec.get("value")) is int else None,
            modifier_state_known=_modifier_state_known(rec),
            has_clipboard=isinstance(rec.get("clipboard"), str),
        )

    for path in _candidate_files(start_local, end_local, ensure=ensure):
        yield from read_jsonl_with(path, _hydrate, source_name="keylog")


def _analysis_records(
    *,
    start: datetime,
    end: datetime,
    ensure: bool = True,
) -> Iterator[_KeylogAnalysisRecord]:
    """Yield the analysis-only union of press metadata and snapshot text."""

    start_local = as_local(start)
    end_local = as_local(end)

    def _hydrate(rec: dict[str, Any]) -> _KeylogAnalysisRecord | None:
        ts = parse_datetime(rec.get("ts"))
        if ts is None:
            return None
        ts_local = as_local(ts)
        if ts_local < start_local or ts_local >= end_local:
            return None
        return _KeylogAnalysisRecord(
            ts=ts_local,
            event=str(rec.get("event") or ""),
            keycode=rec.get("keycode"),
            changed=rec.get("changed") if isinstance(rec.get("changed"), bool) else None,
            modifiers=_modifier_state(rec),
            text=_text_payload(rec),
        )

    for path in _candidate_files(start_local, end_local, ensure=ensure):
        yield from read_jsonl_with(path, _hydrate, source_name="keylog")


def keypresses(*, start: datetime, end: datetime, ensure: bool = True) -> list[KeylogEvent]:
    return list(events(start=start, end=end, kinds={"press"}, ensure=ensure))


def _modifier_state(rec: dict[str, Any]) -> tuple[str, ...]:
    for key in ("modifiers", "active_modifiers", "pressed_modifiers", "modifier_state", "mods"):
        raw = rec.get(key)
        values = _modifier_values(raw)
        if values:
            return values
    return ()


def _modifier_state_known(rec: dict[str, Any]) -> bool:
    return any(
        isinstance(rec.get(key), (str, list, tuple, dict))
        for key in ("modifiers", "active_modifiers", "pressed_modifiers", "modifier_state", "mods")
    )


def _modifier_values(raw: Any) -> tuple[str, ...]:
    if raw in (None, "", False):
        return ()
    tokens: list[str] = []
    if isinstance(raw, str):
        tokens.extend(raw.replace("+", " ").replace(",", " ").split())
    elif isinstance(raw, dict):
        tokens.extend(str(key) for key, value in raw.items() if value)
    elif isinstance(raw, (list, tuple, set)):
        tokens.extend(str(value) for value in raw)
    else:
        return ()
    modifiers = [_normalize_modifier_token(token) for token in tokens]
    return tuple(sorted({modifier for modifier in modifiers if modifier is not None}))


def _normalize_modifier_token(token: str) -> str | None:
    value = token.strip().upper()
    if not value:
        return None
    value = value.removeprefix("KEY_")
    aliases = {
        "LEFTMETA": "SUPER",
        "RIGHTMETA": "SUPER",
        "META": "SUPER",
        "WIN": "SUPER",
        "LOGO": "SUPER",
        "SUPER": "SUPER",
        "LEFTSHIFT": "SHIFT",
        "RIGHTSHIFT": "SHIFT",
        "SHIFT": "SHIFT",
        "LEFTCTRL": "CTRL",
        "RIGHTCTRL": "CTRL",
        "CONTROL": "CTRL",
        "CTRL": "CTRL",
        "LEFTALT": "ALT",
        "RIGHTALT": "ALT",
        "ALT": "ALT",
    }
    return aliases.get(value)


def text_snapshots(
    *,
    start: datetime,
    end: datetime,
    ensure: bool = True,
) -> Iterator[KeylogTextSnapshot]:
    start_local = as_local(start)
    end_local = as_local(end)

    def _hydrate(rec: dict[str, Any]) -> KeylogTextSnapshot | None:
        text = _text_payload(rec)
        if text is None:
            return None
        ts = parse_datetime(rec.get("ts"))
        if ts is None:
            return None
        ts_local = as_local(ts)
        if ts_local < start_local or ts_local >= end_local:
            return None
        return KeylogTextSnapshot(
            ts=ts_local,
            event=str(rec.get("event") or ""),
            session=rec.get("session"),
            window=rec.get("window"),
            text=text,
        )

    for path in _candidate_files(start_local, end_local, ensure=ensure):
        yield from read_jsonl_with(path, _hydrate, source_name="keylog")


def _text_payload(rec: dict[str, Any]) -> str | None:
    for key in ("buffer", "text", "content"):
        value = rec.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def keypress_count(*, start: datetime, end: datetime, ensure: bool = True) -> int:
    return len(keypress_timestamps(start=start, end=end, ensure=ensure))


def keypress_timestamps(
    *, start: datetime, end: datetime, ensure: bool = True
) -> tuple[datetime, ...]:
    """Read bounded press times through the revision-keyed per-file cache."""
    start_local = as_local(start)
    end_local = as_local(end)
    timestamps = []
    for path in _candidate_files(start_local, end_local, ensure=ensure):
        try:
            stat = path.stat()
        except OSError:
            continue
        for ts in _press_timestamps(str(path), stat.st_mtime_ns, stat.st_size):
            if start_local <= ts < end_local:
                timestamps.append(ts)
    return tuple(sorted(timestamps))


@lru_cache(maxsize=512)
def _press_timestamps(path: str, mtime_ns: int, size: int) -> tuple[datetime, ...]:
    _ = (mtime_ns, size)

    def _press_ts(rec: dict[str, Any]) -> datetime | None:
        if rec.get("event") != "press":
            return None
        ts = parse_datetime(rec.get("ts"))
        return as_local(ts) if ts is not None else None

    return tuple(read_jsonl_with(Path(path), _press_ts, source_name="keylog"))


def has_coverage(*, start: datetime, end: datetime, ensure: bool = True) -> bool:
    return bool(_candidate_files(start, end, ensure=ensure))


def daily_activity(
    *,
    start: date,
    end: date,
    ensure: bool = True,
) -> list[KeylogDayActivity]:
    days = list(iter_dates(start, end))
    event_counts = {d: 0 for d in days}
    keypress_counts = {d: 0 for d in days}
    changed_keypress_counts = {d: 0 for d in days}
    sessions: dict[date, set[str]] = {d: set() for d in days}
    timestamps: dict[date, list[datetime]] = {d: [] for d in days}
    start_dt, end_dt = date_to_dt_range(start, end)

    for ev in events(start=start_dt, end=end_dt, ensure=ensure):
        d = logical_date(ev.ts)
        if d not in event_counts:
            continue
        event_counts[d] += 1
        timestamps[d].append(ev.ts)
        if ev.session:
            sessions[d].add(ev.session)
        if ev.event == "press":
            keypress_counts[d] += 1
            if ev.changed is True:
                changed_keypress_counts[d] += 1

    result = []
    for d in days:
        result.append(KeylogDayActivity(
            date=d,
            event_count=event_counts[d],
            keypress_count=keypress_counts[d],
            changed_keypress_count=changed_keypress_counts[d],
            session_count=len(sessions[d]),
            first_ts=min(timestamps[d]) if timestamps[d] else None,
            last_ts=max(timestamps[d]) if timestamps[d] else None,
        ))
    return result


def coverage_bounds() -> CoverageBounds | None:
    logs_dir = get_config().keylog_root / "logs"
    if not logs_dir.exists():
        return None
    stems = sorted(p.stem for p in logs_dir.glob("????-??-??.jsonl"))
    if not stems:
        return None
    try:
        first = date.fromisoformat(stems[0])
        last = date.fromisoformat(stems[-1])
    except ValueError:
        return None
    return CoverageBounds(source="keylog", first=first, last=last, kind="capture")


@dataclass(frozen=True)
class KeylogFileRead:
    path: str
    status: str
    size_bytes: int
    sha256: str | None
    lines: int
    invalid_lines: int
    first_ts: datetime | None
    last_ts: datetime | None
    changed_during_read: bool = False


@dataclass(frozen=True)
class KeylogInputTrace:
    start: datetime
    end: datetime
    events: tuple[KeylogEvent, ...]
    files: tuple[KeylogFileRead, ...]
    event_counts: dict[str, int]
    continuity: str = "unknown"


_WHEEL_CODES = {"REL_WHEEL", "REL_HWHEEL", "REL_WHEEL_HI_RES", "REL_HWHEEL_HI_RES"}
_DISCRETE_EVENTS = {"press", "pointer_button_press", "pointer_axis", "pointer_scroll", "pointer_wheel"}


def read_input_trace(
    *, start: datetime, end: datetime, logs_root: Path | None = None,
) -> KeylogInputTrace:
    """Read bounded input and audit metadata without retaining snapshot text.

    UTC-named day files are read once, hashing their initial byte extent. Missing
    files, malformed records, and growth remain visible; a file does not prove
    uninterrupted capture. Pointer motion is counted but not retained as input.
    This explicit raw-source route never triggers materialization.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("input trace bounds require a timezone")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        raise ValueError("input trace end must follow start")
    root = logs_root if logs_root is not None else _logs_root()
    # Filename selection is UTC calendar storage, not analytical day bucketing.
    cursor = start.date()
    last_day = (end - timedelta(microseconds=1)).date()
    files: list[KeylogFileRead] = []
    retained: list[KeylogEvent] = []
    counts: Counter[str] = Counter()
    while cursor <= last_day:
        path = root / f"{cursor.isoformat()}.jsonl"
        cursor += timedelta(days=1)
        if not path.exists():
            files.append(KeylogFileRead(str(path), "missing", 0, None, 0, 0, None, None))
            continue
        stat = path.stat()
        digest = hashlib.sha256()
        lines = invalid = 0
        first: datetime | None = None
        last: datetime | None = None
        with path.open("rb") as handle:
            remaining = stat.st_size
            while remaining:
                raw = handle.readline(remaining)
                if not raw:
                    break
                remaining -= len(raw)
                digest.update(raw)
                lines += 1
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                    if not isinstance(rec, dict):
                        raise ValueError("non-object record")
                    stamp = rec.get("ts")
                    if not isinstance(stamp, str):
                        raise ValueError("missing timestamp")
                    ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        raise ValueError("naive source timestamp")
                    ts = ts.astimezone(UTC)
                    event = rec.get("event")
                    if not isinstance(event, str):
                        raise ValueError("missing event kind")
                except (ValueError, TypeError, UnicodeError):
                    invalid += 1
                    continue
                first = min(first, ts) if first else ts
                last = max(last, ts) if last else ts
                if not start <= ts < end:
                    continue
                code = rec.get("code") if isinstance(rec.get("code"), str) else None
                value = rec.get("value") if type(rec.get("value")) is int else None
                counts[f"{event}:{code}" if code else event] += 1
                if event not in _DISCRETE_EVENTS and not (
                    event == "pointer_rel" and code in _WHEEL_CODES and value != 0
                ):
                    continue
                retained.append(KeylogEvent(
                    ts=ts,
                    event=event,
                    session=rec.get("session") if isinstance(rec.get("session"), str) else None,
                    window=rec.get("window") if isinstance(rec.get("window"), str) else None,
                    keycode=rec.get("keycode") if isinstance(rec.get("keycode"), str) else None,
                    changed=rec.get("changed") if isinstance(rec.get("changed"), bool) else None,
                    modifiers=_modifier_state(rec),
                    code=code,
                    value=value,
                    modifier_state_known=_modifier_state_known(rec),
                    has_clipboard=isinstance(rec.get("clipboard"), str),
                    source_path=str(path),
                    source_line=lines,
                ))
        after = path.stat()
        changed = (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino)
        files.append(KeylogFileRead(
            str(path), "degraded" if invalid or changed or remaining else "read",
            stat.st_size - remaining, digest.hexdigest(), lines, invalid, first, last, changed,
        ))
    retained.sort(key=lambda event: event.ts)
    return KeylogInputTrace(start, end, tuple(retained), tuple(files), dict(counts))
