from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ...core.cache import file_signature, persistent_cache

from .terminal_capture_types import _CastHeaderSummary, _CastTimingSummary

_REALM_PROJECT_ROOT = Path("/realm/project")
ACTIVE_GAP_SECONDS = 2.0
FULL_CAST_TIMING_SCAN_BYTES = 16 * 1024 * 1024
TAIL_CHUNK_BYTES = 256 * 1024
_CACHE_LOGGER = logging.getLogger(__name__ + ".cachew")
if _CACHE_LOGGER.level == logging.NOTSET:
    _CACHE_LOGGER.setLevel(logging.WARNING)

def _iter_cast_paths(scan_root: Path) -> Iterator[Path]:
    for cast_path in sorted(scan_root.rglob("session.cast")):
        if cast_path.is_file():
            yield cast_path


def _iter_cast_paths_for_window(
    scan_root: Path,
    start: datetime,
    end: datetime,
) -> Iterator[Path]:
    """Yield cast paths only within YYYY/MM/DD directories that overlap [start, end].

    The asciinema tree is organised as scan_root/YYYY/MM/DD/session-dir/session.cast.
    Walking only the relevant day directories avoids stat()-ing the entire corpus
    (typically 1 000+ files) for short time windows.
    """
    # Convert to local dates; extend by 1 day on each side to cover sessions
    # that straddle midnight or span multiple days.
    start_date = (start.astimezone().replace(tzinfo=None) - timedelta(days=1)).date()
    end_date = (end.astimezone().replace(tzinfo=None) + timedelta(days=1)).date()

    try:
        year_dirs = sorted(scan_root.iterdir())
    except OSError:
        return

    for year_dir in year_dirs:
        if not year_dir.is_dir():
            continue
        try:
            year = int(year_dir.name)
        except ValueError:
            continue
        if year < start_date.year or year > end_date.year:
            continue

        try:
            month_dirs = sorted(year_dir.iterdir())
        except OSError:
            continue

        for month_dir in month_dirs:
            if not month_dir.is_dir():
                continue
            try:
                month = int(month_dir.name)
            except ValueError:
                continue
            if (year, month) < (start_date.year, start_date.month):
                continue
            if (year, month) > (end_date.year, end_date.month):
                continue

            try:
                day_dirs = sorted(month_dir.iterdir())
            except OSError:
                continue

            for day_dir in day_dirs:
                if not day_dir.is_dir():
                    continue
                try:
                    day = int(day_dir.name)
                    d = date(year, month, day)
                except (ValueError, TypeError):
                    continue
                if d < start_date or d > end_date:
                    continue

                for cast_path in sorted(day_dir.rglob("session.cast")):
                    if cast_path.is_file():
                        yield cast_path


@persistent_cache(
    "terminal_cast_summaries",
    depends_on=lambda path: file_signature(path),
    logger=_CACHE_LOGGER,
)
def _read_cast_summary(path: Path) -> Optional[_CastHeaderSummary]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            header_line = fh.readline()
            if not header_line:
                return None
    except OSError:
        return None

    try:
        header = json.loads(header_line)
    except json.JSONDecodeError:
        return None

    stat = path.stat()
    duration_seconds: float = 0.0
    active_seconds: Optional[float] = None
    idle_seconds: Optional[float] = None
    timing_source = "tail"

    if stat.st_size <= FULL_CAST_TIMING_SCAN_BYTES:
        duration_seconds, active_seconds, idle_seconds = _scan_cast_timings(path)
        timing_source = "full"
    else:
        last_time = _read_last_cast_timestamp(path)
        if last_time is None:
            duration_seconds, active_seconds, idle_seconds = _scan_cast_timings(path)
            timing_source = "full-fallback"
        else:
            duration_seconds = last_time

    return _CastHeaderSummary(
        header_json=json.dumps(header, ensure_ascii=False, sort_keys=True),
        duration_seconds=duration_seconds,
        active_seconds=active_seconds,
        idle_seconds=idle_seconds,
        timing_source=timing_source,
    )


def _read_cast_header(path: Path) -> tuple[Optional[dict[str, Any]], float, Optional[float], Optional[float], Optional[str]]:
    summary = _read_cast_summary(path)
    if summary is None:
        return None, 0.0, None, None, None
    try:
        header = json.loads(summary.header_json)
    except json.JSONDecodeError:
        return None, 0.0, None, None, None
    return header, summary.duration_seconds, summary.active_seconds, summary.idle_seconds, summary.timing_source


@persistent_cache(
    "terminal_cast_full_timing",
    depends_on=lambda path: file_signature(path),
    logger=_CACHE_LOGGER,
)
def _read_cast_full_timing(path: Path) -> _CastTimingSummary:
    duration_seconds, active_seconds, idle_seconds = _scan_cast_timings(path)
    return _CastTimingSummary(
        duration_seconds=duration_seconds,
        active_seconds=active_seconds,
        idle_seconds=idle_seconds,
    )


def _scan_cast_timings(path: Path) -> tuple[float, Optional[float], Optional[float]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            next(fh, None)
            duration_seconds = 0.0
            active_seconds = 0.0
            idle_seconds = 0.0
            previous_time = 0.0
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, list) and event:
                    timestamp = _to_float(event[0])
                    if timestamp is None or timestamp < 0:
                        continue
                    delta = max(timestamp - previous_time, 0.0)
                    duration_seconds = max(duration_seconds, timestamp)
                    active_seconds += min(delta, ACTIVE_GAP_SECONDS)
                    idle_seconds += max(delta - ACTIVE_GAP_SECONDS, 0.0)
                    previous_time = max(previous_time, timestamp)
            return duration_seconds, active_seconds, idle_seconds
    except OSError:
        return 0.0, None, None


def _read_last_cast_timestamp(path: Path) -> Optional[float]:
    try:
        file_size = path.stat().st_size
        if file_size <= 0:
            return None

        with path.open("rb") as fh:
            buffer = b""
            offset = file_size
            while offset > 0:
                read_size = min(TAIL_CHUNK_BYTES, offset)
                offset -= read_size
                fh.seek(offset)
                buffer = fh.read(read_size) + buffer
                lines = [line.strip() for line in buffer.splitlines() if line.strip()]
                if offset > 0 and len(lines) < 2:
                    continue
                for raw in reversed(lines):
                    try:
                        event = json.loads(raw.decode("utf-8", errors="ignore"))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, list) and event:
                        timestamp = _to_float(event[0])
                        if timestamp is not None and timestamp >= 0:
                            return timestamp
            return None
    except OSError:
        return None



def _session_id(cast_path: Path) -> str:
    return cast_path.parent.name


def _sidecar_paths(cast_path: Path) -> tuple[Path, Path]:
    return (
        cast_path.with_name("session.json"),
        cast_path.with_name("events.jsonl"),
    )


def _schema_generation(
    manifest: Optional[dict[str, Any]],
    header: Optional[dict[str, Any]],
) -> str:
    if manifest:
        return str(manifest.get("schema_generation") or manifest.get("schema") or "terminal-session-v1")
    version = _to_int((header or {}).get("version"))
    if version is not None:
        return f"asciicast-v{version}"
    return "cast-header"


def _manifest_time(manifest: dict[str, Any], iso_key: str, ms_key: str) -> Optional[str]:
    return _to_text(manifest.get(iso_key)) or _local_iso_from_epoch_ms(manifest.get(ms_key))


def _guess_project_root(value: Any) -> Optional[str]:
    text = _to_text(value)
    if not text:
        return None

    try:
        path = Path(text).resolve(strict=False)
    except OSError:
        return None

    if _REALM_PROJECT_ROOT not in path.parents and path != _REALM_PROJECT_ROOT:
        return None

    try:
        relative = path.relative_to(_REALM_PROJECT_ROOT)
    except ValueError:
        return None

    if not relative.parts:
        return None

    return str(_REALM_PROJECT_ROOT / relative.parts[0])


def _session_time_from_id(session_id: str) -> Optional[str]:
    if not session_id:
        return None
    match = re.search(r"(\d{13})$", session_id)
    if match:
        return _local_iso_from_epoch_ms(match.group(1))
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", session_id)
    if match:
        try:
            stamp = f"{match.group(1)}T{match.group(2).replace('-', ':')}"
            return datetime.fromisoformat(stamp).astimezone().isoformat()
        except ValueError:
            return None
    match = re.search(r"(\d{8}T\d{6}Z)$", session_id)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone().isoformat()
        except ValueError:
            return None
    return None


def _duration_between(start_value: Any, end_value: Any) -> Optional[float]:
    start = _parse_iso_datetime(_to_text(start_value))
    end = _parse_iso_datetime(_to_text(end_value))
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


def _assess_session_quality(
    *,
    manifest_exists: bool,
    has_events: bool,
    schema_generation: str,
    created_at: Optional[str],
    finished_at: Optional[str],
    duration_seconds: Optional[float],
    active_seconds: Optional[float],
    command: Optional[str],
    timing_source: Optional[str],
) -> tuple[str, list[str]]:
    flags: list[str] = []

    if not manifest_exists:
        flags.append("missing_manifest")
    if not has_events:
        flags.append("missing_events")
    if not created_at:
        flags.append("missing_created_at")
    if not finished_at:
        flags.append("missing_finished_at")
    if duration_seconds is None:
        flags.append("missing_duration")
    if active_seconds is None:
        flags.append("missing_activity_estimate")
    if not command:
        flags.append("missing_command")
    if timing_source in {"tail", "full-fallback"}:
        flags.append("timing_estimated")
    if timing_source is None:
        flags.append("timing_unavailable")
    if not has_events and not manifest_exists and schema_generation in {"asciicast-v2", "asciicast-v3"}:
        flags.append("header_only")
    if manifest_exists and not has_events:
        flags.append("broken_new_model")

    status = "ok"
    if "broken_new_model" in flags:
        status = "damaged"
    elif "header_only" in flags:
        status = "header-only"
    elif any(
        flag in flags
        for flag in (
            "missing_created_at",
            "missing_finished_at",
            "missing_duration",
            "missing_activity_estimate",
            "timing_unavailable",
        )
    ):
        status = "degraded"

    return status, flags


def _load_json_file(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
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


def _local_iso_from_epoch_seconds(value: Any) -> Optional[str]:
    seconds = _to_float(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _local_iso_from_epoch_ms(value: Any) -> Optional[str]:
    millis = _to_float(value)
    if millis is None:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ms_to_seconds(value: Any) -> Optional[float]:
    millis = _to_float(value)
    if millis is None:
        return None
    return millis / 1000.0


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False
