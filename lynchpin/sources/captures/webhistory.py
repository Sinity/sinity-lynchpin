from __future__ import annotations

import csv
import json
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

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)


@dataclass
class WebHistoryEntry:
    date: str
    record_json: str
    source_file: str

    def to_record(self) -> Dict[str, object]:
        data = json.loads(self.record_json)
        data["_source_file"] = self.source_file
        return data


TRACKING_PREFIXES = {
    "utm_",
    "fbclid",
    "gclid",
    "igshid",
    "yclid",
    "dclid",
    "ref_",
    "spm",
    "sc_",
    "mc_",
    "mkt_",
    "pk_campaign",
    "pk_kwd",
    "ga_",
    "gs_",
    "ved",
    "ei",
    "sa",
    "rlz",
    "dpr",
    "biw",
    "bih",
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
        host = parsed.netloc.lower().lstrip("www.")
        path = parsed.path or "/"
        if path.endswith("/") and len(path) > 1:
            path = path.rstrip("/")
        query = parse_qs(parsed.query, keep_blank_values=True)
        keep = SPECIAL_PARAM_WHITELIST.get(host.split(":")[0], set())
        cleaned = {
            k: v
            for k, v in query.items()
            if k in keep or not any(k.startswith(prefix) for prefix in TRACKING_PREFIXES)
        }
        if host == "youtu.be" and path.lstrip("/"):
            vid = path.lstrip("/")
            host = "youtube.com"
            path = "/watch"
            cleaned.setdefault("v", []).append(vid)
            keep = SPECIAL_PARAM_WHITELIST.get(host, set())
            cleaned = {
                k: v
                for k, v in cleaned.items()
                if k in keep or not any(k.startswith(prefix) for prefix in TRACKING_PREFIXES)
            }
        query_str = urlencode(cleaned, doseq=True)
        rebuilt = f"https://{host}{path}"
        if query_str:
            rebuilt += f"?{query_str}"
        return rebuilt
    except Exception:
        return url.strip()


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
        suffix = file.suffix.lower()
        if suffix == ".csv":
            for visit in _iter_gestalt_csv(file):
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
            continue
        if suffix == ".json":
            for visit in _iter_gestalt_json(file):
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
            continue
        with file.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_value = record.get("visitTime") or record.get("lastVisitTime") or record.get("iso_time")
                ts = _to_datetime(ts_value)
                if not ts:
                    continue
                entries.append(
                    WebHistoryEntry(
                        date=ts.date().isoformat(),
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
) -> Iterator[Dict[str, object]]:
    for entry in _load_entries(root, ndjson):
        if start_date and entry.date < start_date:
            continue
        if end_date and entry.date > end_date:
            continue
        yield entry.to_record()


def _to_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 10**16:
        seconds = numeric / 1_000_000_000
    elif numeric > 10**12:
        seconds = numeric / 1_000
    else:
        seconds = numeric
    try:
        return datetime.fromtimestamp(seconds)
    except (OSError, OverflowError, ValueError):
        return None


@dataclass(frozen=True)
class WebHistoryVisit:
    timestamp: datetime
    url: str
    title: str
    source: str


def summarize_gestalt_dir(
    root: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return summarize_events_by_month(iter_gestalt_events(root), start_month, end_month)


def summarize_ndjson(
    path: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return summarize_events_by_month(iter_ndjson_events(path), start_month, end_month)


def iter_gestalt_events(root: Path) -> Iterator[WebHistoryVisit]:
    if not root.exists():
        return iter(())

    def generator() -> Iterator[WebHistoryVisit]:
        for path in sorted(root.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".csv":
                yield from _iter_gestalt_csv(path)
            elif suffix == ".json":
                yield from _iter_gestalt_json(path)

    return generator()


def iter_ndjson_events(path: Path) -> Iterator[WebHistoryVisit]:
    if not path.exists():
        return iter(())

    def generator() -> Iterator[WebHistoryVisit]:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                ts_raw = obj.get("iso_time")
                if not isinstance(ts_raw, str):
                    continue
                try:
                    dt = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
                url = obj.get("url") if isinstance(obj.get("url"), str) else ""
                title = obj.get("title") if isinstance(obj.get("title"), str) else ""
                yield WebHistoryVisit(timestamp=dt, url=url, title=title, source=str(path))

    return generator()


def summarize_events_by_month(
    events: Iterable[WebHistoryVisit],
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_domains: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_reddit_subs: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_title_tokens: Dict[str, Counter[str]] = defaultdict(Counter)

    for event in events:
        month = _month_key_from_dt(event.timestamp)
        if not _month_key_in_range(month, start_month, end_month):
            continue
        parsed = urlparse(event.url or "")
        domain = _normalize_domain(parsed.netloc)
        if domain:
            per_month_domains[month][domain] += 1
        if event.title:
            for tok in _tokenize_topic(event.title):
                per_month_title_tokens[month][tok] += 1
        counts[month] += 1
        if domain in {"reddit.com", "old.reddit.com", "new.reddit.com"}:
            parts = parsed.path.strip("/").split("/", 3)
            if len(parts) >= 2 and parts[0] == "r" and parts[1]:
                per_month_reddit_subs[month][parts[1].lower()] += 1

    return counts, per_month_domains, per_month_reddit_subs, per_month_title_tokens


def _iter_gestalt_csv(path: Path) -> Iterator[WebHistoryVisit]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row:
                continue
            dt = _parse_webhistory_csv_dt(row.get("date", ""), row.get("time", ""))
            if dt is None:
                continue
            url = row.get("url", "") or ""
            title = row.get("title", "") or ""
            yield WebHistoryVisit(timestamp=dt, url=url, title=title, source=str(path))


def _iter_gestalt_json(path: Path) -> Iterator[WebHistoryVisit]:
    with path.open("r", encoding="utf-8") as fh:
        first_nonempty: str | None = None
        for line in fh:
            raw = line.strip()
            if raw:
                first_nonempty = raw
                break
        if first_nonempty is None:
            return

        if first_nonempty.startswith("["):
            fh.seek(0)
            try:
                payload = json.load(fh)
            except json.JSONDecodeError:
                return
            if not isinstance(payload, list):
                return
            for obj in payload:
                if not isinstance(obj, dict):
                    continue
                dt = _parse_webhistory_json_dt(obj.get("visitTime") or obj.get("lastVisitTime") or obj.get("time"))
                if dt is None:
                    continue
                url = obj.get("url") if isinstance(obj.get("url"), str) else ""
                title = obj.get("title") if isinstance(obj.get("title"), str) else ""
                yield WebHistoryVisit(timestamp=dt, url=url, title=title, source=str(path))
            return

        for raw in (first_nonempty,):
            raw = raw.strip()
            if raw:
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    dt = _parse_webhistory_json_dt(obj.get("visitTime") or obj.get("lastVisitTime") or obj.get("time"))
                    if dt is not None:
                        url = obj.get("url") if isinstance(obj.get("url"), str) else ""
                        title = obj.get("title") if isinstance(obj.get("title"), str) else ""
                        yield WebHistoryVisit(timestamp=dt, url=url, title=title, source=str(path))

        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            dt = _parse_webhistory_json_dt(obj.get("visitTime") or obj.get("lastVisitTime") or obj.get("time"))
            if dt is None:
                continue
            url = obj.get("url") if isinstance(obj.get("url"), str) else ""
            title = obj.get("title") if isinstance(obj.get("title"), str) else ""
            yield WebHistoryVisit(timestamp=dt, url=url, title=title, source=str(path))


def _parse_webhistory_csv_dt(date_raw: str, time_raw: str) -> datetime | None:
    date_raw = (date_raw or "").strip()
    time_raw = (time_raw or "").strip()
    if not date_raw or not time_raw:
        return None
    stamp = f"{date_raw} {time_raw}"
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M:%S", "%m/%d/%y %H:%M"):
        try:
            return datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_webhistory_json_dt(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _month_key_from_dt(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_key_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month


def _normalize_domain(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[\w]+", text.lower(), flags=re.UNICODE)
    return [t for t in tokens if t]


_TOPIC_STOPWORDS = {
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
    "i",
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
    "a",
    "ale",
    "bo",
    "by",
    "byc",
    "co",
    "czy",
    "do",
    "dla",
    "i",
    "jak",
    "ja",
    "jest",
    "juz",
    "mnie",
    "na",
    "nie",
    "od",
    "o",
    "po",
    "sie",
    "sa",
    "ta",
    "tak",
    "to",
    "tu",
    "w",
    "we",
    "wy",
    "za",
    "ze",
}


def _tokenize_topic(text: str) -> List[str]:
    out: List[str] = []
    for tok in _tokenize(text):
        if tok in _TOPIC_STOPWORDS:
            continue
        if len(tok) < 3:
            continue
        if tok.isdigit():
            continue
        out.append(tok)
    return out
