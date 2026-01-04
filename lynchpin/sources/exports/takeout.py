from __future__ import annotations

import csv
import io
import json
import re
import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

MYACTIVITY_EVENT_RE = re.compile(
    r'<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">(.*?)<br>([^<]+)</div>',
    re.DOTALL,
)
MYACTIVITY_ANCHOR_RE = re.compile(r'<a href="([^"]+)">(.*?)</a>', re.DOTALL)
MYACTIVITY_DATE_RE = re.compile(r"\b([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4})\b")
MYACTIVITY_FULL_DT_RE = re.compile(
    r"\b([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4}),\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)\b"
)
MYACTIVITY_CONTENT_CELL_START = '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
MYACTIVITY_BR = "<br>"
MYACTIVITY_DIV_END = "</div>"


def month_key_from_date(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_key_from_dt(dt: datetime) -> str:
    return month_key_from_date(dt.date())


def month_key_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month


def normalize_domain(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc


def tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[\w]+", text.lower(), flags=re.UNICODE)
    return [t for t in tokens if t]


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
    # Polish (minimal, just to reduce noise)
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


def tokenize_topic(text: str) -> List[str]:
    out: List[str] = []
    for tok in tokenize(text):
        if tok in _TOPIC_STOPWORDS:
            continue
        if len(tok) < 3:
            continue
        if tok.isdigit():
            continue
        out.append(tok)
    return out


def decode_mime_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


@dataclass(frozen=True)
class MyActivityEvent:
    category: str
    month: str
    action: str
    href: Optional[str]
    text: str
    date_text: str
    timestamp: str


def normalize_myactivity_whitespace(text: str) -> str:
    out = html_unescape(text or "")
    out = out.replace("\u00a0", " ").strip()
    out = re.sub(r"\s+", " ", out)
    return out


def myactivity_timestamp_key(date_text: str) -> str:
    normalized = normalize_myactivity_whitespace(date_text)
    full = MYACTIVITY_FULL_DT_RE.search(normalized)
    if not full:
        return normalized
    month, day, year, hour, minute, second, ampm = full.groups()
    hour_i = int(hour)
    if ampm.upper() == "PM" and hour_i != 12:
        hour_i += 12
    if ampm.upper() == "AM" and hour_i == 12:
        hour_i = 0
    return f"{year}-{MONTHS.get(month, 1):02d}-{int(day):02d} {hour_i:02d}:{minute}:{second}"


def canonicalize_myactivity_href(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    parsed = urlparse(href)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        qs = parse_qs(parsed.query)
        if "q" in qs and qs["q"]:
            href = qs["q"][0]
        elif "url" in qs and qs["url"]:
            href = qs["url"][0]
    return unquote(href)


def extract_youtube_video_id(url: str) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    host = normalize_domain(parsed.netloc)
    if host.endswith("youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            vid = (qs.get("v") or [None])[0]
            if vid:
                return vid
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/", 2)[2] if len(parsed.path.split("/")) >= 3 else None
    if host == "youtu.be":
        return parsed.path.strip("/") or None
    return None


def month_from_myactivity_date(date_text: str) -> Optional[str]:
    normalized = normalize_myactivity_whitespace(date_text)
    m = MYACTIVITY_DATE_RE.search(normalized)
    if not m:
        return None
    month_name, day, year = m.groups()
    month_i = MONTHS.get(month_name)
    if not month_i:
        return None
    return f"{int(year):04d}-{month_i:02d}"


def iter_myactivity_events(html: str, category: str) -> Iterator[MyActivityEvent]:
    for m in MYACTIVITY_EVENT_RE.finditer(html or ""):
        cell_html, date_text = m.groups()
        if not cell_html or not date_text:
            continue
        month = month_from_myactivity_date(date_text)
        if not month:
            continue
        timestamp = myactivity_timestamp_key(date_text)

        # Example: "Searched for <a href=...>foo</a>"
        anchor = MYACTIVITY_ANCHOR_RE.search(cell_html)
        href: Optional[str] = None
        text = normalize_myactivity_whitespace(cell_html)
        action = text
        if anchor:
            href = html_unescape(anchor.group(1)).strip()
            anchor_text = html_unescape(re.sub(r"<[^>]+>", "", anchor.group(2))).strip()
            action_prefix = html_unescape(cell_html[: anchor.start()]).replace("\u00a0", " ").strip()
            action = action_prefix or normalize_myactivity_whitespace(cell_html)
            text = anchor_text

        yield MyActivityEvent(
            category=category,
            month=month,
            action=action,
            href=href,
            text=text,
            date_text=date_text,
            timestamp=timestamp,
        )


class TarReader:
    def __init__(self, tar_path: Path):
        self.tar_path = tar_path
        self._tf: Optional[tarfile.TarFile] = None
        self._members: Dict[str, tarfile.TarInfo] = {}

    def __enter__(self) -> "TarReader":
        self._tf = tarfile.open(self.tar_path)
        self._members = {m.name: m for m in self._tf.getmembers()}
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        if self._tf is not None:
            self._tf.close()
        self._tf = None
        self._members = {}

    def open(self, member_path: str) -> Optional[io.BufferedReader]:
        if self._tf is None:
            raise RuntimeError("TarReader not opened (use as a context manager).")
        member = self._members.get(member_path)
        if member is None:
            return None
        return self._tf.extractfile(member)

    def read_text(self, member_path: str) -> Optional[str]:
        fh = self.open(member_path)
        if fh is None:
            return None
        data = fh.read()
        return data.decode("utf-8", errors="replace")

    def iter_members(self) -> Iterable[tarfile.TarInfo]:
        return self._members.values()

    def has_member(self, member_path: str) -> bool:
        return member_path in self._members

    def member_size(self, member_path: str) -> int | None:
        member = self._members.get(member_path)
        return member.size if member is not None else None


def parse_myactivity_from_takeouts(
    takeouts: List[TarReader],
    category: str,
    member_path: str,
    start_month: str,
    end_month: str,
    include_actions: Optional[Tuple[str, ...]] = None,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    """Return counts + token/phrase counters for query-like events."""
    counts: Dict[str, int] = defaultdict(int)
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_phrases: Dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[Tuple[str, str, str, str]] = set()

    for tar in takeouts:
        html = tar.read_text(member_path)
        if html is None:
            continue
        for event in iter_myactivity_events(html, category=category):
            if event.month > end_month:
                continue
            if event.month < start_month:
                break
            if include_actions is not None and not any(event.action.startswith(a) for a in include_actions):
                continue
            if event.action.startswith("Searched for"):
                content_key = event.text
            else:
                content_key = canonicalize_myactivity_href(event.href or "") or event.text
            key = (event.category, event.action, event.timestamp, content_key)
            if key in seen:
                continue
            seen.add(key)
            counts[event.month] += 1
            if event.action.startswith("Searched for") and event.text:
                per_month_phrases[event.month][event.text] += 1
                for tok in tokenize(event.text):
                    per_month_tokens[event.month][tok] += 1
    return counts, per_month_tokens, per_month_phrases


def normalize_myactivity_category_dir_name(name: str) -> str:
    """Normalize MyActivity directory names across takeout variants."""
    return (name or "").strip().replace(" _ ", " & ")


def discover_myactivity_category_member_paths(takeouts: List[TarReader]) -> Dict[str, set[str]]:
    """Return display-category -> member paths across all takeouts."""
    by_category: Dict[str, set[str]] = defaultdict(set)
    for tar in takeouts:
        for member in tar.iter_members():
            if not member.isfile():
                continue
            name = member.name
            if not (name.startswith("Takeout/My Activity/") and name.endswith("/MyActivity.html")):
                continue
            parts = name.split("/")
            if len(parts) < 4:
                continue
            display = normalize_myactivity_category_dir_name(parts[2])
            if not display:
                continue
            by_category[display].add(name)
    return by_category


def parse_myactivity_counts_from_takeouts_member_paths(
    takeouts: List[TarReader],
    category: str,
    member_paths: List[str],
    start_month: str,
    end_month: str,
    include_actions: Optional[Tuple[str, ...]] = None,
) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    seen: set[Tuple[str, str, str, str]] = set()

    for member_path in member_paths:
        for tar in takeouts:
            html = tar.read_text(member_path)
            if html is None:
                continue
            for event in iter_myactivity_events(html, category=category):
                if event.month > end_month:
                    continue
                if event.month < start_month:
                    break
                if include_actions is not None and not any(event.action.startswith(a) for a in include_actions):
                    continue
                if event.action.startswith("Searched for"):
                    content_key = event.text
                else:
                    content_key = canonicalize_myactivity_href(event.href or "") or event.text
                key = (event.category, event.action, event.timestamp, content_key)
                if key in seen:
                    continue
                seen.add(key)
                counts[event.month] += 1
    return counts


def parse_myactivity_other_category_counts_from_takeouts(
    takeouts: List[TarReader],
    start_month: str,
    end_month: str,
    *,
    exclude_categories: set[str],
) -> Dict[str, Counter[str]]:
    """Return month -> Counter(category -> count) for non-core MyActivity categories."""
    per_month: Dict[str, Counter[str]] = defaultdict(Counter)
    member_paths_by_category = discover_myactivity_category_member_paths(takeouts)
    for display_category, member_paths in sorted(member_paths_by_category.items(), key=lambda kv: kv[0].lower()):
        if display_category in exclude_categories:
            continue
        counts = parse_myactivity_counts_from_takeouts_member_paths(
            takeouts=takeouts,
            category=f"myactivity:{display_category}",
            member_paths=sorted(member_paths),
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        for month, count in counts.items():
            if count:
                per_month[month][display_category] += count
    return per_month


def parse_myactivity_text_events_from_takeouts(
    takeouts: List[TarReader],
    category: str,
    member_path: str,
    start_month: str,
    end_month: str,
    *,
    include_actions: Optional[Tuple[str, ...]] = None,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    """Return counts + token/phrase counters for event anchor text."""
    counts: Dict[str, int] = defaultdict(int)
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_phrases: Dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[Tuple[str, str, str, str]] = set()

    for tar in takeouts:
        html = tar.read_text(member_path)
        if html is None:
            continue
        for event in iter_myactivity_events(html, category=category):
            if event.month > end_month:
                continue
            if event.month < start_month:
                break
            if include_actions is not None and not any(event.action.startswith(a) for a in include_actions):
                continue
            content_key = event.text or canonicalize_myactivity_href(event.href or "") or ""
            key = (event.category, event.action, event.timestamp, content_key)
            if key in seen:
                continue
            seen.add(key)
            counts[event.month] += 1
            if event.text:
                per_month_phrases[event.month][event.text] += 1
                for tok in tokenize_topic(event.text):
                    per_month_tokens[event.month][tok] += 1
    return counts, per_month_tokens, per_month_phrases


def load_youtube_video_titles_from_takeout(takeout: TarReader, member_path: str) -> Dict[str, str]:
    fh = takeout.open(member_path)
    if fh is None:
        return {}
    wrapper = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
    reader = csv.DictReader(wrapper)
    titles: Dict[str, str] = {}
    for row in reader:
        video_id = (row.get("Video ID") or "").strip()
        title = (row.get("Video Title Text Segments 1") or "").strip()
        if not video_id or not title:
            continue
        titles.setdefault(video_id, title)
    return titles


def parse_youtube_watch_history_from_takeouts(
    takeouts: List[TarReader],
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    """Parse Takeout watch-history.html into per-month counts + IDs + titles + channels."""
    counts: Dict[str, int] = defaultdict(int)
    per_month_video_ids: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_titles: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_channels: Dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[Tuple[str, str, str]] = set()

    def strip_tags(fragment: str) -> str:
        return html_unescape(re.sub(r"<[^>]+>", "", fragment)).strip()

    def parse_anchor(fragment: str) -> tuple[str, str]:
        anchor = MYACTIVITY_ANCHOR_RE.search(fragment)
        if not anchor:
            return "", ""
        href = html_unescape(anchor.group(1)).strip()
        text = html_unescape(re.sub(r"<[^>]+>", "", anchor.group(2))).strip()
        return href, text

    def is_youtube_channel_href(href: str) -> bool:
        parsed = urlparse((href or "").strip())
        host = normalize_domain(parsed.netloc)
        if not host.endswith("youtube.com"):
            return False
        path = parsed.path or ""
        return path.startswith("/channel/") or path.startswith("/@") or path.startswith("/user/") or path.startswith("/c/")

    def iter_watch_history_rows(html: str) -> Iterator[tuple[str, str, str, str | None, str | None, str | None]]:
        pos = 0
        while True:
            start = html.find(MYACTIVITY_CONTENT_CELL_START, pos)
            if start == -1:
                break
            start_content = start + len(MYACTIVITY_CONTENT_CELL_START)
            div_end = html.find(MYACTIVITY_DIV_END, start_content)
            if div_end == -1:
                break
            cell = html[start_content:div_end]
            pos = div_end + len(MYACTIVITY_DIV_END)

            parts = [p for p in cell.split(MYACTIVITY_BR) if p and p.strip()]
            if not parts:
                continue

            date_text: str | None = None
            date_idx: int | None = None
            for idx, part in enumerate(parts):
                candidate = strip_tags(part)
                if month_from_myactivity_date(candidate):
                    date_text = candidate
                    date_idx = idx
                    break
            if date_text is None:
                continue
            month = month_from_myactivity_date(date_text)
            if not month:
                continue
            timestamp = myactivity_timestamp_key(date_text)

            line1 = parts[0]
            a_start = line1.find("<a")
            href, title = ("", "")
            if a_start != -1:
                href, title = parse_anchor(line1[a_start:])
            prefix = html_unescape(line1[:a_start] if a_start != -1 else line1)
            action = prefix.replace("\u00a0", " ").strip() or strip_tags(line1)
            if not action:
                continue

            channel: str | None = None
            if date_idx is not None and date_idx > 0:
                for part in parts[1:date_idx]:
                    ch_href, ch_label = parse_anchor(part)
                    if ch_label and is_youtube_channel_href(ch_href):
                        channel = ch_label
                        break

            yield month, timestamp, action, (canonicalize_myactivity_href(href) if href else None), (title or None), channel

    member_path = "Takeout/YouTube and YouTube Music/history/watch-history.html"
    for tar in takeouts:
        html = tar.read_text(member_path)
        if html is None:
            continue
        for month, timestamp, action, href, title, channel in iter_watch_history_rows(html):
            if month > end_month:
                continue
            if month < start_month:
                break
            if not action.startswith("Watched"):
                continue
            vid = extract_youtube_video_id(href or "")
            key = (action, timestamp, vid or href or title or "")
            if key in seen:
                continue
            seen.add(key)
            counts[month] += 1
            if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                per_month_video_ids[month][vid] += 1
            if title:
                per_month_titles[month][title] += 1
            if channel:
                per_month_channels[month][channel] += 1

    return counts, per_month_video_ids, per_month_titles, per_month_channels


def parse_chrome_history_json_from_takeout(
    takeout: TarReader,
    member_path: str,
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    """Parse `Takeout/Chrome/History.json` into monthly browsing signals."""
    counts: Dict[str, int] = defaultdict(int)
    per_month_domains: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_reddit_subs: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_title_tokens: Dict[str, Counter[str]] = defaultdict(Counter)

    raw = takeout.read_text(member_path)
    if raw is None:
        return counts, per_month_domains, per_month_reddit_subs, per_month_title_tokens
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return counts, per_month_domains, per_month_reddit_subs, per_month_title_tokens

    items = payload.get("Browser History") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return counts, per_month_domains, per_month_reddit_subs, per_month_title_tokens

    for item in items:
        if not isinstance(item, dict):
            continue
        time_usec = item.get("time_usec")
        if not isinstance(time_usec, int):
            continue
        dt = datetime.fromtimestamp(time_usec / 1_000_000, tz=timezone.utc)
        month = month_key_from_dt(dt)
        if not month_key_in_range(month, start_month, end_month):
            continue
        url = item.get("url") if isinstance(item.get("url"), str) else ""
        title = item.get("title") if isinstance(item.get("title"), str) else ""
        parsed = urlparse(url)
        domain = normalize_domain(parsed.netloc)
        if domain:
            per_month_domains[month][domain] += 1
        if title:
            for tok in tokenize_topic(title):
                per_month_title_tokens[month][tok] += 1
        counts[month] += 1
        if domain in {"reddit.com", "old.reddit.com", "new.reddit.com"}:
            parts = parsed.path.strip("/").split("/", 3)
            if len(parts) >= 2 and parts[0] == "r" and parts[1]:
                per_month_reddit_subs[month][parts[1].lower()] += 1
    return counts, per_month_domains, per_month_reddit_subs, per_month_title_tokens


def parse_location_records_from_takeout(
    tar: TarReader, member_path: str, start_month: str, end_month: str
) -> Dict[str, int]:
    """Stream-parse Location History Records.json counts by month."""
    counts: Dict[str, int] = defaultdict(int)
    fh = tar.open(member_path)
    if fh is None:
        return counts
    for raw in fh:
        line = raw.decode("utf-8", errors="replace")
        if not line.startswith('    "timestamp": '):
            continue
        m = re.search(r'"timestamp":\s+"(\d{4}-\d{2}-\d{2})T', line)
        if not m:
            continue
        y, mth, _ = (int(part) for part in m.group(1).split("-", 2))
        month = f"{y:04d}-{mth:02d}"
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
    return counts


def parse_semantic_location_history_from_takeout(
    tar: TarReader,
    root_prefix: str,
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    """Parse Semantic Location History month files and aggregate counts + top items."""
    place_visits: Dict[str, int] = defaultdict(int)
    activity_segments: Dict[str, int] = defaultdict(int)
    per_month_places: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_activities: Dict[str, Counter[str]] = defaultdict(Counter)

    month_name_map = {
        "JAN": 1,
        "FEB": 2,
        "MAR": 3,
        "APR": 4,
        "MAY": 5,
        "JUN": 6,
        "JUL": 7,
        "AUG": 8,
        "SEP": 9,
        "OCT": 10,
        "NOV": 11,
        "DEC": 12,
        "JANUARY": 1,
        "FEBRUARY": 2,
        "MARCH": 3,
        "APRIL": 4,
        "MAY": 5,
        "JUNE": 6,
        "JULY": 7,
        "AUGUST": 8,
        "SEPTEMBER": 9,
        "OCTOBER": 10,
        "NOVEMBER": 11,
        "DECEMBER": 12,
    }

    for member in tar.iter_members():
        if not member.isfile():
            continue
        if not member.name.startswith(root_prefix):
            continue
        if not member.name.endswith(".json"):
            continue
        tail = member.name.split("/")[-1]
        m = re.match(r"(\d{4})_([A-Z]+)\.json$", tail)
        if not m:
            continue
        year = int(m.group(1))
        month_name = m.group(2)
        month_i = month_name_map.get(month_name)
        if not month_i:
            continue
        month = f"{year:04d}-{month_i:02d}"
        if not month_key_in_range(month, start_month, end_month):
            continue
        fh = tar.open(member.name)
        if fh is None:
            continue
        payload = json.loads(fh.read().decode("utf-8", errors="replace"))
        for obj in payload.get("timelineObjects") or []:
            if "placeVisit" in obj:
                place_visits[month] += 1
                loc = obj["placeVisit"].get("location") or {}
                address = (loc.get("address") or "").strip()
                if address:
                    per_month_places[month][address] += 1
            if "activitySegment" in obj:
                activity_segments[month] += 1
                act = obj["activitySegment"].get("activityType")
                if act:
                    per_month_activities[month][act] += 1
    return place_visits, activity_segments, per_month_places, per_month_activities


def parse_gmail_headers_from_takeout_mbox(
    tar: TarReader,
    member_path: str,
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    """Stream-parse Gmail mbox and extract counts/domains/subject tokens by month."""
    counts: Dict[str, int] = defaultdict(int)
    per_month_from_domains: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_subject_tokens: Dict[str, Counter[str]] = defaultdict(Counter)

    fh = tar.open(member_path)
    if fh is None:
        return counts, per_month_from_domains, per_month_subject_tokens

    in_headers = False
    headers: Dict[str, str] = {}
    current_key: Optional[str] = None

    def flush_message() -> None:
        nonlocal headers
        date_raw = headers.get("date", "").strip()
        if not date_raw:
            headers = {}
            return
        try:
            dt = parsedate_to_datetime(date_raw)
        except Exception:
            headers = {}
            return
        month = month_key_from_dt(dt)
        if not month_key_in_range(month, start_month, end_month):
            headers = {}
            return
        counts[month] += 1

        from_raw = decode_mime_header(headers.get("from", ""))
        for domain in re.findall(r"@([A-Za-z0-9._-]+)", from_raw):
            per_month_from_domains[month][domain.lower()] += 1

        subj = decode_mime_header(headers.get("subject", ""))
        for tok in tokenize(subj):
            per_month_subject_tokens[month][tok] += 1

        headers = {}

    for raw in fh:
        line = raw.decode("utf-8", errors="replace")
        if line.startswith("From "):
            if headers:
                flush_message()
            headers = {}
            current_key = None
            in_headers = True
            continue
        if not in_headers:
            continue
        if line in {"\n", "\r\n"}:
            in_headers = False
            current_key = None
            continue
        if line[0] in {" ", "\t"} and current_key:
            headers[current_key] = (headers.get(current_key, "") + " " + line.strip()).strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        current_key = key
        if key in {"date", "from", "subject"}:
            headers[key] = value

    if headers:
        flush_message()
    return counts, per_month_from_domains, per_month_subject_tokens


def expand_takeout_parts(path: Path) -> List[Path]:
    """Expand a `...-001.tgz` seed path into all sibling takeout parts."""
    if not path.exists():
        return []
    name = path.name
    if name.endswith(".tgz"):
        stem = name[:-4]
        m = re.match(r"^(?P<prefix>.+)-(?P<part>\d{3})$", stem)
        if m:
            prefix = m.group("prefix")
            parts = sorted(path.parent.glob(f"{prefix}-*.tgz"))
            return [p for p in parts if p.exists()]
    return [path]
