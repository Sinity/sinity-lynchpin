from __future__ import annotations

import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

from ...core.cache import files_digest, persistent_cache
from ...core.config import get_config
from .webhistory_common import (
    CHROME_CSV_LOCAL_TZ,
    WEBHISTORY_TIMESTAMP_FIELDS,
    payload_timestamp,
    parse_webhistory_timestamp,
)

logger = logging.getLogger(__name__)

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class WebHistoryEntry:
    date: str
    record_json: str
    source_file: str

    def to_record(self) -> Dict[str, object]:
        data = json.loads(self.record_json)
        data["_source_file"] = self.source_file
        return data


@dataclass(frozen=True)
class WebHistoryVisit:
    timestamp: datetime
    url: str
    title: str
    source: str


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

TRACKING_PREFIXES = {
    "utm_", "fbclid", "gclid", "igshid", "yclid", "dclid", "ref_", "spm",
    "sc_", "mc_", "mkt_", "pk_campaign", "pk_kwd", "ga_", "gs_",
    "ved", "ei", "sa", "rlz", "dpr", "biw", "bih",
}

SPECIAL_PARAM_WHITELIST = {
    "youtube.com": {"v", "list", "t"},
    "youtu.be": {"v", "t"},
    "github.com": {"ref", "sha"},
    "reddit.com": {"sort", "type", "t"},
    "twitter.com": {"s", "q"},
}


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        if not parsed.netloc:
            return url.strip()
        scheme = (parsed.scheme or "https").lower()
        if scheme not in ("http", "https"):
            return url.strip()
        host = _normalize_domain(parsed.netloc)
        path = parsed.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        query = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = _strip_tracking_params(query, host)
        if host == "youtu.be" and path.lstrip("/"):
            vid = path.lstrip("/")
            host = "youtube.com"
            path = "/watch"
            if "v" not in cleaned:
                cleaned["v"] = [vid]
            cleaned = _strip_tracking_params(cleaned, host)
        query_str = urlencode(cleaned, doseq=True)
        rebuilt = f"https://{host}{path}"
        if query_str:
            rebuilt += f"?{query_str}"
        return rebuilt
    except Exception:
        return url.strip()


def _strip_tracking_params(query: dict, host: str) -> dict:
    keep = SPECIAL_PARAM_WHITELIST.get(host.split(":")[0], set())
    return {
        k: v for k, v in query.items()
        if k in keep or not any(k.startswith(p) for p in TRACKING_PREFIXES)
    }


def _normalize_domain(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc


# ---------------------------------------------------------------------------
# Cached entry loading (used by warehouse)
# ---------------------------------------------------------------------------


def _history_files(root: Optional[Path] = None, ndjson: Optional[Path] = None) -> List[Path]:
    cfg = get_config()
    fallback = ndjson or cfg.webhistory_ndjson
    if root is None and fallback and Path(fallback).exists():
        return [Path(fallback)]
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
    if fallback and Path(fallback).exists():
        return [Path(fallback)]
    return []


@persistent_cache(
    "webhistory_entries",
    depends_on=lambda root=None, ndjson=None: files_digest(_history_files(root, ndjson)),
)
def _load_entries(root: Optional[Path], ndjson: Optional[Path]) -> List[WebHistoryEntry]:
    entries: List[WebHistoryEntry] = []
    for file in _history_files(root, ndjson):
        for visit in _iter_file_visits(file):
            record = {
                "url": visit.url,
                "title": visit.title,
                "iso_time": visit.timestamp.isoformat(),
                "source": file.name,
            }
            entries.append(WebHistoryEntry(
                date=visit.timestamp.date().isoformat(),
                record_json=json.dumps(record, ensure_ascii=False),
                source_file=str(file),
            ))
    return entries


def iter_entries(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    root: Optional[Path] = None,
    ndjson: Optional[Path] = None,
) -> Iterator[Dict[str, object]]:
    for entry in _load_entries(root, ndjson):
        if start_date and entry.date < start_date:
            continue
        if end_date and entry.date > end_date:
            continue
        yield entry.to_record()


# ---------------------------------------------------------------------------
# Visit iterators (used by ingest, summarize, and full_history)
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
            url = row.get("url") or row.get("NavigatedToUrl") or row.get("navigatedtourl") or ""
            title = row.get("title") or row.get("PageTitle") or row.get("pagetitle") or ""
            yield WebHistoryVisit(timestamp=dt, url=url, title=title, source=str(path))


def _iter_json_visits(path: Path) -> Iterator[WebHistoryVisit]:
    """Handle JSON arrays, single objects, and NDJSON/JSONL lines."""
    source = str(path)
    suffix = path.suffix.lower()

    # JSONL/NDJSON: always line-by-line
    if suffix in {".jsonl", ".ndjson"}:
        yield from _iter_jsonl_visits(path, source)
        return

    # .json: try array parse first, fall back to line-by-line
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

    # Shouldn't reach here, but fall back to line-by-line
    yield from _iter_jsonl_visits(path, source)


def _iter_jsonl_visits(path: Path, source: str) -> Iterator[WebHistoryVisit]:
    parse_errors = 0
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        yield from _iter_jsonl_from_handle(fh, source)


def _iter_jsonl_from_handle(fh, source: str) -> Iterator[WebHistoryVisit]:
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


def _visit_from_dict(obj: dict, source: str) -> Optional[WebHistoryVisit]:
    dt = payload_timestamp(obj)
    if dt is None:
        return None
    url = obj.get("url") if isinstance(obj.get("url"), str) else ""
    title = obj.get("title") if isinstance(obj.get("title"), str) else ""
    return WebHistoryVisit(timestamp=dt, url=url, title=title, source=source)


# ---------------------------------------------------------------------------
# CSV timestamp parsing
# ---------------------------------------------------------------------------


def _parse_csv_dt(row: dict) -> Optional[datetime]:
    # Chrome CSV: date + time columns (local time)
    date_raw = (row.get("date") or "").strip()
    time_raw = (row.get("time") or "").strip()
    if date_raw and time_raw:
        stamp = f"{date_raw} {time_raw}"
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M:%S", "%m/%d/%y %H:%M"):
            try:
                naive = datetime.strptime(stamp, fmt)
                return naive.replace(tzinfo=CHROME_CSV_LOCAL_TZ).astimezone(timezone.utc)
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
# Summarization (used by calendar views and trajectory)
# ---------------------------------------------------------------------------


SummarizationResult = Tuple[
    Dict[str, int],
    Dict[str, Counter[str]],
    Dict[str, Counter[str]],
    Dict[str, Counter[str]],
]


def summarize_gestalt_dir(root: Path, start_month: str, end_month: str) -> SummarizationResult:
    return summarize_events_by_month(iter_gestalt_events(root), start_month, end_month)


def summarize_ndjson(path: Path, start_month: str, end_month: str) -> SummarizationResult:
    return summarize_events_by_month(iter_ndjson_events(path), start_month, end_month)


def summarize_events_by_month(
    events: Iterable[WebHistoryVisit], start_month: str, end_month: str,
) -> SummarizationResult:
    counts: Dict[str, int] = defaultdict(int)
    per_month_domains: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_reddit_subs: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_title_tokens: Dict[str, Counter[str]] = defaultdict(Counter)

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
# Text tokenization (for title analysis)
# ---------------------------------------------------------------------------

_TOPIC_STOPWORDS = {
    # English
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "he", "her", "his", "if", "in", "is", "it", "its", "me",
    "my", "not", "of", "on", "or", "our", "ours", "she", "so", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "was", "we",
    "were", "what", "when", "where", "which", "who", "why", "will", "with",
    "you", "your",
    # Polish
    "ale", "bo", "byc", "co", "czy", "dla", "jak", "ja", "jest", "juz",
    "mnie", "na", "nie", "od", "po", "sie", "sa", "ta", "tak", "tu", "wy",
    "za", "ze",
}


def _tokenize_topic(text: str) -> List[str]:
    return [
        tok for tok in re.findall(r"[\w]+", text.lower(), flags=re.UNICODE)
        if tok not in _TOPIC_STOPWORDS and len(tok) >= 3 and not tok.isdigit()
    ]
