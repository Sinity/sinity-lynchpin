from __future__ import annotations

import re
import tarfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from ...core.cache import file_signature, persistent_cache


SAMSUNG_SLEEP_MEMBER = "samsunghealth_ezo.dev_20240813122209/com.samsung.shealth.sleep.20240813122209.csv"
SAMSUNG_WEIGHT_MEMBER = "samsunghealth_ezo.dev_20240813122209/com.samsung.health.weight.20240813122209.csv"
SAMSUNG_SLEEP_PATTERN = "com.samsung.shealth.sleep.[0-9]*.csv"
SAMSUNG_WEIGHT_PATTERN = "com.samsung.health.weight*.csv"
SAMSUNG_SLEEP_PREFIX = "/com.samsung.shealth.sleep."
SAMSUNG_WEIGHT_PREFIX = "/com.samsung.health.weight."


@dataclass(frozen=True)
class SamsungSleepSession:
    start_time: datetime
    duration_minutes: float


@dataclass(frozen=True)
class SamsungWeightEntry:
    recorded_at: datetime
    weight: float


def _month_key_from_dt(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_key_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month


def _safe_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _is_tar(path: Path) -> bool:
    return path.suffix.lower() in {".tar", ".tgz"} or path.name.endswith(".tar.gz")


def _latest_dated_subdir(root: Path) -> Optional[Path]:
    candidates = []
    for entry in root.iterdir():
        if entry.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", entry.name):
            candidates.append(entry)
    if not candidates:
        return None
    return sorted(candidates)[-1]


def _resolve_samsung_csv(export_path: Path, pattern: str) -> Optional[Path]:
    if export_path.is_file():
        if export_path.suffix.lower() == ".csv":
            return export_path
        return None
    if not export_path.exists():
        return None
    search_root = export_path
    latest = _latest_dated_subdir(export_path)
    if latest is not None:
        search_root = latest
    matches = sorted(search_root.rglob(pattern))
    if matches:
        return matches[-1]
    return None


def _samsung_cache_signature(
    export_path: Path,
    member_path: str,
    pattern: str,
) -> Tuple[Tuple[str, int | None, int | None], str]:
    if export_path.is_dir():
        resolved = _resolve_samsung_csv(export_path, pattern)
        if resolved is not None:
            return file_signature(resolved), pattern
    return file_signature(export_path), member_path


def _iter_samsung_rows_from_handle(handle) -> Iterator[Dict[str, str]]:
    first = handle.readline()
    if not first:
        return iter(())
    header = handle.readline()
    if not header:
        return iter(())
    columns = header.decode("utf-8", errors="replace").strip("\n").split(",")

    def generator() -> Iterator[Dict[str, str]]:
        for raw in handle:
            row = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not row:
                continue
            parts = row.split(",")
            if len(parts) < len(columns):
                parts.extend([""] * (len(columns) - len(parts)))
            yield dict(zip(columns, parts))

    return generator()


def _iter_samsung_rows_from_path(path: Path) -> Iterator[Dict[str, str]]:
    with path.open("rb") as handle:
        yield from _iter_samsung_rows_from_handle(handle)


@persistent_cache(
    "samsung_sleep_sessions",
    depends_on=lambda export_path, member_path=SAMSUNG_SLEEP_MEMBER, pattern=SAMSUNG_SLEEP_PATTERN: _samsung_cache_signature(
        export_path, member_path, pattern
    ),
)
def _load_samsung_sleep_sessions(
    export_path: Path,
    *,
    member_path: str = SAMSUNG_SLEEP_MEMBER,
    pattern: str = SAMSUNG_SLEEP_PATTERN,
) -> List[SamsungSleepSession]:
    if not export_path.exists():
        return []
    sessions: List[SamsungSleepSession] = []
    if export_path.is_dir() or export_path.suffix.lower() == ".csv":
        csv_path = export_path if export_path.suffix.lower() == ".csv" else _resolve_samsung_csv(export_path, pattern)
        if csv_path is None:
            return []
        rows = _iter_samsung_rows_from_path(csv_path)
    elif _is_tar(export_path):
        with tarfile.open(export_path) as tf:
            member = _find_member(tf, member_path, SAMSUNG_SLEEP_PREFIX)
            if member is None:
                return []
            rows = _iter_samsung_rows(tf, member)
    else:
        return []

    for row in rows:
        start_raw = row.get("com.samsung.health.sleep.start_time") or row.get(
            "com.samsung.shealth.sleep.start_time"
        )
        duration_raw = row.get("sleep_duration") or ""
        if not start_raw or not duration_raw:
            continue
        dt = _parse_samsung_dt(start_raw)
        if dt is None:
            continue
        duration = _safe_float(duration_raw)
        if duration is None or duration <= 0:
            continue
        sessions.append(SamsungSleepSession(start_time=dt, duration_minutes=duration))
    return sessions


def iter_samsung_sleep_sessions(
    export_path: Path,
    *,
    member_path: str = SAMSUNG_SLEEP_MEMBER,
    pattern: str = SAMSUNG_SLEEP_PATTERN,
) -> Iterator[SamsungSleepSession]:
    return iter(_load_samsung_sleep_sessions(export_path, member_path=member_path, pattern=pattern))


@persistent_cache(
    "samsung_weight_entries",
    depends_on=lambda export_path, member_path=SAMSUNG_WEIGHT_MEMBER, pattern=SAMSUNG_WEIGHT_PATTERN: _samsung_cache_signature(
        export_path, member_path, pattern
    ),
)
def _load_samsung_weight_entries(
    export_path: Path,
    *,
    member_path: str = SAMSUNG_WEIGHT_MEMBER,
    pattern: str = SAMSUNG_WEIGHT_PATTERN,
) -> List[SamsungWeightEntry]:
    if not export_path.exists():
        return []
    entries: List[SamsungWeightEntry] = []
    if export_path.is_dir() or export_path.suffix.lower() == ".csv":
        csv_path = export_path if export_path.suffix.lower() == ".csv" else _resolve_samsung_csv(export_path, pattern)
        if csv_path is None:
            return []
        rows = _iter_samsung_rows_from_path(csv_path)
    elif _is_tar(export_path):
        with tarfile.open(export_path) as tf:
            member = _find_member(tf, member_path, SAMSUNG_WEIGHT_PREFIX)
            if member is None:
                return []
            rows = _iter_samsung_rows(tf, member)
    else:
        return []

    for row in rows:
        time_raw = row.get("start_time") or ""
        weight_raw = row.get("weight") or ""
        if not time_raw or not weight_raw:
            continue
        dt = _parse_samsung_dt(time_raw)
        if dt is None:
            continue
        weight = _safe_float(weight_raw)
        if weight is None:
            continue
        entries.append(SamsungWeightEntry(recorded_at=dt, weight=weight))
    return entries


def iter_samsung_weight_entries(
    export_path: Path,
    *,
    member_path: str = SAMSUNG_WEIGHT_MEMBER,
    pattern: str = SAMSUNG_WEIGHT_PATTERN,
) -> Iterator[SamsungWeightEntry]:
    return iter(_load_samsung_weight_entries(export_path, member_path=member_path, pattern=pattern))


def parse_samsung_health_sleep(
    export_path: Path,
    start_month: str,
    end_month: str,
    *,
    member_path: str = SAMSUNG_SLEEP_MEMBER,
    pattern: str = SAMSUNG_SLEEP_PATTERN,
) -> Tuple[Dict[str, int], Dict[str, float]]:
    sessions: Dict[str, int] = defaultdict(int)
    total_hours: Dict[str, float] = defaultdict(float)
    for session in iter_samsung_sleep_sessions(export_path, member_path=member_path, pattern=pattern):
        month = _month_key_from_dt(session.start_time)
        if not _month_key_in_range(month, start_month, end_month):
            continue
        sessions[month] += 1
        total_hours[month] += session.duration_minutes / 60.0
    return sessions, total_hours


def parse_samsung_health_weight(
    export_path: Path,
    start_month: str,
    end_month: str,
    *,
    member_path: str = SAMSUNG_WEIGHT_MEMBER,
    pattern: str = SAMSUNG_WEIGHT_PATTERN,
) -> Dict[str, List[float]]:
    weights: Dict[str, List[float]] = defaultdict(list)
    for entry in iter_samsung_weight_entries(export_path, member_path=member_path, pattern=pattern):
        month = _month_key_from_dt(entry.recorded_at)
        if not _month_key_in_range(month, start_month, end_month):
            continue
        weights[month].append(entry.weight)
    return weights


def _find_member(
    tf: tarfile.TarFile,
    member_path: str,
    fallback_prefix: str,
) -> Optional[tarfile.TarInfo]:
    try:
        return tf.getmember(member_path)
    except KeyError:
        matches = [
            member
            for member in tf.getmembers()
            if member.isfile()
            and member.name.endswith(".csv")
            and fallback_prefix in member.name
        ]
        if not matches:
            return None
        matches.sort(key=lambda m: m.name)
        return matches[0]


def _iter_samsung_rows(
    tf: tarfile.TarFile,
    member: tarfile.TarInfo,
) -> Iterator[Dict[str, str]]:
    fh = tf.extractfile(member)
    if fh is None:
        return iter(())

    def generator() -> Iterator[Dict[str, str]]:
        with fh:
            yield from _iter_samsung_rows_from_handle(fh)

    return generator()


def _parse_samsung_dt(value: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
