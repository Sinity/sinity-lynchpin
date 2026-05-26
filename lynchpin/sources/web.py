"""Webhistory source and normalization helpers.

Read-only access over the canonical webhistory roots:
- raw exports under `/realm/data/captures/webhistory/gestalt/raw/`
- deduped canonical segments under `/realm/data/captures/webhistory/gestalt/data/`
- merged NDJSON under `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson`

Use ``lynchpin.ingest.webhistory`` for extract-dedup-merge workflows
over browser data. This module stays purely read-only.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import date as _date_type, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, TextIO
from urllib.parse import urlparse

from ..core.cache import file_digest, files_digest, persistent_cache
from ..core.config import get_config
from .web_models import (
    WebDayActivity,
    WebHistoryEntry,
    WebHistoryRawEntry,
    WebHistoryVisit,
    _WebDayBucket,
)
from .web_timestamps import (
    CHROME_CSV_LOCAL_TZ,
    WEBHISTORY_TIMESTAMP_FIELDS,
    parse_webhistory_timestamp,
    payload_timestamp,
)
from .web_urls import _normalize_domain, normalize_url

logger = logging.getLogger(__name__)

__all__ = [
    "WebHistoryEntry",
    "WebHistoryVisit",
    "WebHistoryRawEntry",
    "parse_webhistory_timestamp",
    "payload_timestamp",
    "normalize_url",
    "iter_entries",
    "raw_files",
    "iter_raw_entries",
    "iter_raw_file_entries",
    "load_raw_file",
    "iter_gestalt_events",
    "iter_file_visits",
    "iter_ndjson_events",
    "summarize_gestalt_dir",
    "summarize_ndjson",
    "summarize_events_by_month",
    "WebDayActivity",
    "daily_browsing",
    "domain_breakdown",
]

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)


# ---------------------------------------------------------------------------
# Constants (from webhistory_common.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Data types (from webhistory.py)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# URL normalization (from webhistory.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cached entry loading (from webhistory.py)
# ---------------------------------------------------------------------------


def _history_files(
    root: Optional[Path] = None, ndjson: Optional[Path] = None
) -> list[Path]:
    cfg = get_config()
    canonical = ndjson or cfg.webhistory_ndjson
    if root is None:
        if canonical is None:
            raise FileNotFoundError(
                "canonical webhistory NDJSON is not configured; run python -m lynchpin.ingest.webhistory"
            )
        canonical_path = Path(canonical)
        if not canonical_path.exists():
            raise FileNotFoundError(
                f"canonical webhistory NDJSON is missing: {canonical_path}. "
                "Run python -m lynchpin.ingest.webhistory to materialize it."
            )
        return [canonical_path]
    path = root or cfg.webhistory_dir
    if path.exists():
        candidates = [
            *sorted(path.glob("*.jsonl")),
            *sorted(path.glob("*.ndjson")),
            *sorted(path.glob("*.json")),
            *sorted(path.glob("*.csv")),
        ]
        if candidates:
            return candidates
    return []


def _history_files_signature(*args, **kwargs) -> object:
    # Cachew's composite_hash invokes depends_on with whatever positional /
    # keyword args were passed to the wrapped function. Older shape was
    # ``def _history_files_signature(root=None, ndjson=None)`` which broke when
    # the wrapped function was called positionally (cachew also injected the
    # same arg as a keyword, producing
    # ``got multiple values for argument 'root'``). Accepting *args/**kwargs
    # makes the signature unambiguous; we normalize internally.
    root = kwargs.get("root")
    ndjson = kwargs.get("ndjson")
    if args:
        if len(args) > 0:
            root = args[0] if root is None else root
        if len(args) > 1:
            ndjson = args[1] if ndjson is None else ndjson
    return files_digest(_history_files(root=root, ndjson=ndjson))


@persistent_cache("webhistory_entries", depends_on=_history_files_signature)
def _load_entries(
    root: Optional[Path] = None, ndjson: Optional[Path] = None
) -> list[WebHistoryEntry]:
    entries: list[WebHistoryEntry] = []
    for file in _history_files(root, ndjson):
        for visit in _iter_file_visits(file):
            record = {
                "url": visit.url,
                "title": visit.title,
                "iso_time": visit.timestamp.isoformat(),
                "source": file.name,
            }
            entries.append(
                WebHistoryEntry(
                    date=visit.timestamp.date().isoformat(),
                    record_json=json.dumps(record, ensure_ascii=False),
                    source_file=str(file),
                )
            )
    return entries


def iter_entries(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    root: Optional[Path] = None,
    ndjson: Optional[Path] = None,
) -> Iterator[dict[str, object]]:
    for entry in _load_entries(root, ndjson):
        if start_date and entry.date < start_date:
            continue
        if end_date and entry.date > end_date:
            continue
        yield entry.to_record()


# ---------------------------------------------------------------------------
# Raw file loading (from webhistory_raw.py)
# ---------------------------------------------------------------------------

_RAW_SUFFIX_PRIORITY = {
    ".jsonl": 0,
    ".ndjson": 0,
    ".json": 1,
    ".csv": 2,
}


def raw_files(
    root: Optional[Path] = None,
    files: Optional[list[str]] = None,
) -> list[Path]:
    cfg = get_config()
    base = root or cfg.webhistory_raw_dir
    if files:
        paths: list[Path] = []
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
    return sorted(
        candidates,
        key=lambda p: (p.stem, _RAW_SUFFIX_PRIORITY.get(p.suffix.lower(), 99), p.name),
    )


def _raw_file_signature(
    path: Path, signature: tuple[str, int | None, int | None, str | None]
) -> object:
    return path, signature


@persistent_cache("webhistory_raw_file", depends_on=_raw_file_signature)
def _load_raw_file(
    path: Path,
    signature: tuple[str, int | None, int | None, str | None],
) -> list[WebHistoryRawEntry]:
    entries: list[WebHistoryRawEntry] = []
    suffix = path.suffix.lower()
    if suffix in {".json", ".ndjson", ".jsonl"}:
        entries.extend(_load_raw_json(path))
    elif suffix == ".csv":
        entries.extend(_load_raw_csv(path))
    else:
        raise ValueError(f"Unsupported webhistory file: {path}")
    return entries


def iter_raw_entries(
    root: Optional[Path] = None,
    files: Optional[list[str]] = None,
) -> Iterator[WebHistoryRawEntry]:
    for path in raw_files(root, files):
        for entry in load_raw_file(path):
            yield entry


def iter_raw_file_entries(
    root: Optional[Path] = None,
    files: Optional[list[str]] = None,
) -> Iterator[tuple[Path, list[WebHistoryRawEntry]]]:
    for path in raw_files(root, files):
        yield path, load_raw_file(path)


def load_raw_file(
    path: Path,
    signature: Optional[tuple[str, int | None, int | None, str | None]] = None,
) -> list[WebHistoryRawEntry]:
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
    entries: list[WebHistoryRawEntry] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row:
                continue
            dt = None
            if row.get("DateTime"):
                dt = parse_webhistory_timestamp(row["DateTime"])
            if not dt and row.get("date") and row.get("time"):
                dt = parse_webhistory_timestamp(f"{row['date']} {row['time']}")
            if not dt:
                for field in WEBHISTORY_TIMESTAMP_FIELDS:
                    key = field.lower()
                    if key in row and row[key]:
                        dt = parse_webhistory_timestamp(row[key])
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
                row.get("title") or row.get("pagetitle") or row.get("PageTitle") or ""
            )
            payload = dict(row)
            entries.append(
                _make_entry(dt, str(url or ""), str(title or ""), payload, path)
            )
    return entries


def _entries_from_objects(
    objs: Iterable[object], path: Path
) -> Iterator[WebHistoryRawEntry]:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        entry = _entry_from_payload(obj, path)
        if entry:
            yield entry


def _entries_from_lines(
    lines: Iterable[str], path: Path
) -> Iterator[WebHistoryRawEntry]:
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


def _entry_from_payload(
    payload: dict[str, object], path: Path
) -> WebHistoryRawEntry | None:
    dt = None
    for field in WEBHISTORY_TIMESTAMP_FIELDS:
        if field in payload and payload[field] not in (None, ""):
            dt = parse_webhistory_timestamp(payload[field])
            if dt:
                break
    if not dt:
        return None
    raw_url = payload.get("url")
    raw_title = payload.get("title")
    url = raw_url if isinstance(raw_url, str) else ""
    title = raw_title if isinstance(raw_title, str) else ""
    return _make_entry(dt, url, title, payload, path)


def _make_entry(
    dt: datetime, url: str, title: str, payload: dict[str, object], path: Path
) -> WebHistoryRawEntry:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHROME_CSV_LOCAL_TZ)
    payload_json = json.dumps(payload, ensure_ascii=False)
    return WebHistoryRawEntry(
        timestamp=dt.astimezone(timezone.utc),
        url=url or "",
        title=title or "",
        payload_json=payload_json,
        source_file=str(path),
    )


# ---------------------------------------------------------------------------
# Visit iterators (from webhistory.py)
# ---------------------------------------------------------------------------


def iter_gestalt_events(root: Path) -> Iterator[WebHistoryVisit]:
    if not root.exists():
        return
    for path in sorted(root.iterdir()):
        if path.is_file():
            yield from _iter_file_visits(path)


def iter_ndjson_events(path: Path) -> Iterator[WebHistoryVisit]:
    if not path.exists():
        return
    yield from _iter_jsonl_visits(path, str(path))


def iter_file_visits(path: Path) -> Iterator[WebHistoryVisit]:
    if not path.exists():
        return
    yield from _iter_file_visits(path)


def _iter_file_visits(path: Path) -> Iterator[WebHistoryVisit]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        yield from _iter_csv_visits(path)
    elif suffix in {".json", ".jsonl", ".ndjson"}:
        yield from _iter_json_visits(path)


def _iter_csv_visits(path: Path) -> Iterator[WebHistoryVisit]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if not row:
                continue
            dt = _parse_csv_dt(row)
            if dt is None:
                continue
            url = (
                row.get("url")
                or row.get("NavigatedToUrl")
                or row.get("navigatedtourl")
                or ""
            )
            title = (
                row.get("title") or row.get("PageTitle") or row.get("pagetitle") or ""
            )
            yield WebHistoryVisit(timestamp=dt, url=url, title=title, source=str(path))


def _iter_json_visits(path: Path) -> Iterator[WebHistoryVisit]:
    """Handle JSON arrays, single objects, and NDJSON/JSONL lines."""
    source = str(path)
    suffix = path.suffix.lower()

    # JSONL/NDJSON: always line-by-line
    if suffix in {".jsonl", ".ndjson"}:
        yield from _iter_jsonl_visits(path, source)
        return

    # .json: try array parse first, then parse as JSONL/NDJSON.
    with path.open("r", encoding="utf-8") as fh:
        fh.seek(0)
        try:
            payload = json.load(fh)
        except json.JSONDecodeError:
            fh.seek(0)
            yield from _iter_jsonl_from_handle(fh, source)
            return

        if isinstance(payload, dict):
            payload = [payload]
        if isinstance(payload, list):
            for obj in payload:
                visit = _visit_from_dict(obj, source)
                if visit:
                    yield visit
            return

    # Shouldn't reach here, but parse as JSONL/NDJSON as a final shape check.
    yield from _iter_jsonl_visits(path, source)


def _iter_jsonl_visits(path: Path, source: str) -> Iterator[WebHistoryVisit]:
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        yield from _iter_jsonl_from_handle(fh, source)


def _iter_jsonl_from_handle(fh: TextIO, source: str) -> Iterator[WebHistoryVisit]:
    parse_errors = 0
    for line in fh:
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if not isinstance(obj, dict):
            continue
        visit = _visit_from_dict(obj, source)
        if visit:
            yield visit
    if parse_errors:
        logger.warning("webhistory: %d JSON parse errors in %s", parse_errors, source)


def _visit_from_dict(obj: dict[str, Any], source: str) -> Optional[WebHistoryVisit]:
    dt = payload_timestamp(obj)
    if dt is None:
        return None
    raw_url = obj.get("url")
    raw_title = obj.get("title")
    url = raw_url if isinstance(raw_url, str) else ""
    title = raw_title if isinstance(raw_title, str) else ""
    return WebHistoryVisit(timestamp=dt, url=url, title=title, source=source)


def _parse_csv_dt(row: dict[str, str | None]) -> Optional[datetime]:
    # Chrome CSV: date + time columns (local time)
    date_raw = (row.get("date") or "").strip()
    time_raw = (row.get("time") or "").strip()
    if date_raw and time_raw:
        stamp = f"{date_raw} {time_raw}"
        for fmt in (
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%y %H:%M:%S",
            "%m/%d/%y %H:%M",
        ):
            try:
                naive = datetime.strptime(stamp, fmt)
                return naive.replace(tzinfo=CHROME_CSV_LOCAL_TZ).astimezone(
                    timezone.utc
                )
            except ValueError:
                continue

    # Edge CSV: DateTime column (ISO with timezone)
    if row.get("DateTime"):
        dt = parse_webhistory_timestamp(row["DateTime"])
        if dt:
            return dt

    # Fallback: try all known timestamp fields
    for field in WEBHISTORY_TIMESTAMP_FIELDS:
        value = row.get(field)
        if value not in (None, ""):
            dt = parse_webhistory_timestamp(value)
            if dt is not None:
                return dt
    return None


# ---------------------------------------------------------------------------
# Summarization (from webhistory.py)
# ---------------------------------------------------------------------------


SummarizationResult = tuple[
    dict[str, int],
    dict[str, Counter[str]],
    dict[str, Counter[str]],
    dict[str, Counter[str]],
]


def summarize_gestalt_dir(
    root: Path, start_month: str, end_month: str
) -> SummarizationResult:
    return summarize_events_by_month(iter_gestalt_events(root), start_month, end_month)


def summarize_ndjson(
    path: Path, start_month: str, end_month: str
) -> SummarizationResult:
    return summarize_events_by_month(iter_ndjson_events(path), start_month, end_month)


def summarize_events_by_month(
    events: Iterable[WebHistoryVisit],
    start_month: str,
    end_month: str,
) -> SummarizationResult:
    counts: dict[str, int] = defaultdict(int)
    per_month_domains: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_reddit_subs: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_title_tokens: dict[str, Counter[str]] = defaultdict(Counter)

    for event in events:
        month = f"{event.timestamp.year:04d}-{event.timestamp.month:02d}"
        if not (start_month <= month <= end_month):
            continue
        domain = _normalize_domain(urlparse(event.url or "").netloc)
        if domain:
            per_month_domains[month][domain] += 1
        if event.title:
            for tok in _tokenize_topic(event.title):
                per_month_title_tokens[month][tok] += 1
        counts[month] += 1
        if domain in {"reddit.com", "old.reddit.com", "new.reddit.com"}:
            parts = urlparse(event.url or "").path.strip("/").split("/", 3)
            if len(parts) >= 2 and parts[0] == "r" and parts[1]:
                per_month_reddit_subs[month][parts[1].lower()] += 1

    return counts, per_month_domains, per_month_reddit_subs, per_month_title_tokens


# ---------------------------------------------------------------------------
# Text tokenization (from webhistory.py)
# ---------------------------------------------------------------------------


_TOPIC_STOPWORDS = {
    # English
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "he",
    "her",
    "his",
    "if",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "not",
    "of",
    "on",
    "or",
    "our",
    "ours",
    "she",
    "so",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "you",
    "your",
    # Polish
    "ale",
    "bo",
    "byc",
    "co",
    "czy",
    "dla",
    "jak",
    "ja",
    "jest",
    "juz",
    "mnie",
    "na",
    "nie",
    "od",
    "po",
    "sie",
    "sa",
    "ta",
    "tak",
    "tu",
    "wy",
    "za",
    "ze",
}


def _tokenize_topic(text: str) -> list[str]:
    return [
        tok
        for tok in re.findall(r"[\w]+", text.lower(), flags=re.UNICODE)
        if tok not in _TOPIC_STOPWORDS and len(tok) >= 3 and not tok.isdigit()
    ]


# ---------------------------------------------------------------------------
# Daily browsing aggregation
# ---------------------------------------------------------------------------


def _iter_all_visits(
    start: Optional[_date_type] = None,
    end: Optional[_date_type] = None,
) -> Iterator[WebHistoryVisit]:
    """Yield WebHistoryVisit from canonical NDJSON, filtered by date range."""
    cfg = get_config()
    if cfg.webhistory_ndjson is None:
        raise FileNotFoundError(
            "canonical webhistory NDJSON is not configured; run python -m lynchpin.ingest.webhistory"
        )
    if not cfg.webhistory_ndjson.exists():
        raise FileNotFoundError(
            f"canonical webhistory NDJSON is missing: {cfg.webhistory_ndjson}. "
            "Run python -m lynchpin.ingest.webhistory to materialize it."
        )
    source = iter_ndjson_events(cfg.webhistory_ndjson)
    for v in source:
        if v.timestamp is None:
            continue
        d = v.timestamp.date()
        if start and d < start:
            continue
        if end and d > end:
            continue
        yield v


def daily_browsing(*, start: _date_type, end: _date_type) -> list[WebDayActivity]:
    """Daily web browsing aggregation: visits, domains, top sites."""
    by_day: defaultdict[_date_type, _WebDayBucket] = defaultdict(_WebDayBucket)
    for v in _iter_all_visits(start=start, end=end):
        d = v.timestamp.date()
        bucket = by_day[d]
        bucket.count += 1
        domain = _normalize_domain(urlparse(v.url or "").netloc)
        if domain:
            bucket.domains.add(domain)
        if v.title:
            bucket.titles.add(v.title[:80])

    result: list[WebDayActivity] = []
    for d in sorted(by_day):
        bucket = by_day[d]
        result.append(
            WebDayActivity(
                date=d,
                visit_count=bucket.count,
                unique_domains=len(bucket.domains._counts),
                top_domains=bucket.domains.items,
                top_titles=tuple(t for t, _ in bucket.titles.items),
            )
        )
    return result


def domain_breakdown(
    *,
    start: _date_type,
    end: _date_type,
    top_n: int = 20,
) -> list[tuple[str, int, float]]:
    """Top domains by visit count over a date range: (domain, count, pct)."""
    domains: Counter[str] = Counter()
    total = 0
    for v in _iter_all_visits(start=start, end=end):
        domain = _normalize_domain(urlparse(v.url or "").netloc)
        if domain:
            domains[domain] += 1
            total += 1
    return [
        (domain, count, round(count / total, 4) if total else 0)
        for domain, count in domains.most_common(top_n)
    ]
