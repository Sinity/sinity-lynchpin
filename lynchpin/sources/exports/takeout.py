from __future__ import annotations

import csv
import io
import json
import re
import tarfile
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
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


@dataclass(frozen=True)
class LifeTimelineTakeoutBundle:
    google_search_counts: Dict[str, int]
    google_search_tokens: Dict[str, Counter[str]]
    google_search_phrases: Dict[str, Counter[str]]
    youtube_watch_counts: Dict[str, int]
    youtube_search_counts: Dict[str, int]
    youtube_search_tokens: Dict[str, Counter[str]]
    youtube_search_phrases: Dict[str, Counter[str]]
    youtube_video_titles: Dict[str, str]
    youtube_watch_history_counts: Dict[str, int]
    youtube_watch_history_video_ids: Dict[str, Counter[str]]
    youtube_watch_history_titles: Dict[str, Counter[str]]
    youtube_watch_history_channels: Dict[str, Counter[str]]
    youtube_search_history_counts: Dict[str, int]
    youtube_search_history_tokens: Dict[str, Counter[str]]
    youtube_search_history_phrases: Dict[str, Counter[str]]
    chrome_counts: Dict[str, int]
    maps_counts: Dict[str, int]
    maps_tokens: Dict[str, Counter[str]]
    maps_phrases: Dict[str, Counter[str]]
    image_search_counts: Dict[str, int]
    image_search_tokens: Dict[str, Counter[str]]
    image_search_phrases: Dict[str, Counter[str]]
    play_store_counts: Dict[str, int]
    play_store_tokens: Dict[str, Counter[str]]
    play_store_phrases: Dict[str, Counter[str]]
    video_search_counts: Dict[str, int]
    video_search_tokens: Dict[str, Counter[str]]
    video_search_phrases: Dict[str, Counter[str]]
    shopping_counts: Dict[str, int]
    shopping_tokens: Dict[str, Counter[str]]
    shopping_phrases: Dict[str, Counter[str]]
    travel_counts: Dict[str, int]
    travel_tokens: Dict[str, Counter[str]]
    travel_phrases: Dict[str, Counter[str]]
    myactivity_other_counts: Dict[str, Counter[str]]
    chrome_history_counts: Dict[str, int]
    chrome_history_domains: Dict[str, Counter[str]]
    chrome_history_reddit_subs: Dict[str, Counter[str]]
    chrome_history_title_tokens: Dict[str, Counter[str]]
    location_records: Dict[str, int]
    semantic_place_visits: Dict[str, int]
    semantic_activity_segments: Dict[str, int]
    semantic_top_places: Dict[str, Counter[str]]
    semantic_top_activities: Dict[str, Counter[str]]
    gmail_counts: Dict[str, int]
    gmail_from_domains: Dict[str, Counter[str]]
    gmail_subject_tokens: Dict[str, Counter[str]]
    location_takeout_path: str | None
    gmail_takeout_path: str | None
    chrome_history_takeout_path: str | None
    youtube_video_texts_takeout_path: str | None


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


def summarize_youtube_watch_history_month(
    video_ids: Counter[str],
    titles: Counter[str],
    channels: Counter[str],
    *,
    takeout_titles: Dict[str, str],
    oembed_cache: Dict[str, dict[str, Any]],
    tokenize_text: Callable[[str], Sequence[str]] = tokenize_topic,
) -> tuple[list[tuple[str, int]], Counter[str], Counter[str], Counter[str]]:
    top_video_ids = list(video_ids.most_common(15))
    resolved_titles = Counter(titles)
    resolved_channels = Counter(channels)

    if not resolved_channels:
        for vid, count in video_ids.items():
            if not isinstance(vid, str) or not vid:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                continue
            _, channel = resolve_youtube_video_meta(
                vid,
                takeout_titles=takeout_titles,
                oembed_cache=oembed_cache,
            )
            if channel:
                resolved_channels[channel] += count

    if not resolved_titles:
        for vid, count in video_ids.items():
            if not isinstance(vid, str) or not vid:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                continue
            title, _ = resolve_youtube_video_meta(
                vid,
                takeout_titles=takeout_titles,
                oembed_cache=oembed_cache,
            )
            if title:
                resolved_titles[title] += count
            else:
                resolved_titles[vid] += count

    title_tokens: Counter[str] = Counter()
    for title, count in resolved_titles.items():
        if not isinstance(title, str) or not title:
            continue
        for tok in tokenize_text(title):
            title_tokens[tok] += count

    return top_video_ids, resolved_titles, resolved_channels, title_tokens


def phrase_topic_tokens(
    phrases: Counter[str],
    *,
    tokenize_text: Callable[[str], Sequence[str]] = tokenize_topic,
    limit: int = 200,
) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for phrase, count in phrases.most_common(limit):
        for tok in tokenize_text(phrase):
            tokens[tok] += count
    return tokens


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


def parse_life_timeline_takeouts(
    takeout_paths: List[Path],
    *,
    start_month: str,
    end_month: str,
) -> LifeTimelineTakeoutBundle:
    with ExitStack() as stack:
        takeouts = [stack.enter_context(TarReader(path)) for path in takeout_paths]

        google_search_counts, google_search_tokens, google_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="search",
            member_path="Takeout/My Activity/Search/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Searched for",),
        )
        youtube_watch_counts, _, _ = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="youtube",
            member_path="Takeout/My Activity/YouTube/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Watched",),
        )
        youtube_search_counts, youtube_search_tokens, youtube_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="youtube",
            member_path="Takeout/My Activity/YouTube/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Searched for",),
        )

        youtube_video_titles: Dict[str, str] = {}
        youtube_video_texts_takeout_path: str | None = None
        youtube_video_text_takeout = select_archive_with_member(
            takeouts,
            "Takeout/YouTube and YouTube Music/video metadata/video texts.csv",
        )
        if youtube_video_text_takeout is not None:
            youtube_video_texts_takeout_path = str(youtube_video_text_takeout.tar_path)
            youtube_video_titles = load_youtube_video_titles_from_takeout(
                youtube_video_text_takeout,
                member_path="Takeout/YouTube and YouTube Music/video metadata/video texts.csv",
            )

        (
            youtube_watch_history_counts,
            youtube_watch_history_video_ids,
            youtube_watch_history_titles,
            youtube_watch_history_channels,
        ) = parse_youtube_watch_history_from_takeouts(
            takeouts=takeouts,
            start_month=start_month,
            end_month=end_month,
        )
        (
            youtube_search_history_counts,
            youtube_search_history_tokens,
            youtube_search_history_phrases,
        ) = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="youtube_search_history",
            member_path="Takeout/YouTube and YouTube Music/history/search-history.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=("Searched for",),
        )
        chrome_counts, _, _ = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="chrome",
            member_path="Takeout/My Activity/Chrome/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        maps_counts, maps_tokens, maps_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="maps",
            member_path="Takeout/My Activity/Maps/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        image_search_counts, image_search_tokens, image_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="image_search",
            member_path="Takeout/My Activity/Image Search/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        play_store_counts, play_store_tokens, play_store_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="play_store",
            member_path="Takeout/My Activity/Google Play Store/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        video_search_counts, video_search_tokens, video_search_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="video_search",
            member_path="Takeout/My Activity/Video Search/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        shopping_counts, shopping_tokens, shopping_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="shopping",
            member_path="Takeout/My Activity/Shopping/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )
        travel_counts, travel_tokens, travel_phrases = parse_myactivity_from_takeouts(
            takeouts=takeouts,
            category="travel",
            member_path="Takeout/My Activity/Travel/MyActivity.html",
            start_month=start_month,
            end_month=end_month,
            include_actions=None,
        )

        core_myactivity_categories = {
            "Search",
            "YouTube",
            "Chrome",
            "Maps",
            "Image Search",
            "Google Play Store",
            "Video Search",
            "Shopping",
            "Travel",
        }
        myactivity_other_counts = parse_myactivity_other_category_counts_from_takeouts(
            takeouts=takeouts,
            start_month=start_month,
            end_month=end_month,
            exclude_categories=core_myactivity_categories,
        )

        chrome_history_takeout_path: str | None = None
        chrome_history_takeout = select_archive_with_member(takeouts, "Takeout/Chrome/History.json")
        if chrome_history_takeout is not None:
            chrome_history_takeout_path = str(chrome_history_takeout.tar_path)
            (
                chrome_history_counts,
                chrome_history_domains,
                chrome_history_reddit_subs,
                chrome_history_title_tokens,
            ) = parse_chrome_history_json_from_takeout(
                chrome_history_takeout,
                member_path="Takeout/Chrome/History.json",
                start_month=start_month,
                end_month=end_month,
            )
        else:
            chrome_history_counts = defaultdict(int)
            chrome_history_domains = defaultdict(Counter)
            chrome_history_reddit_subs = defaultdict(Counter)
            chrome_history_title_tokens = defaultdict(Counter)

        location_takeout_path: str | None = None
        location_takeout = select_archive_with_member(takeouts, "Takeout/Location History/Records.json")
        if location_takeout is not None:
            location_takeout_path = str(location_takeout.tar_path)
            location_records = parse_location_records_from_takeout(
                location_takeout,
                member_path="Takeout/Location History/Records.json",
                start_month=start_month,
                end_month=end_month,
            )
            (
                semantic_place_visits,
                semantic_activity_segments,
                semantic_top_places,
                semantic_top_activities,
            ) = parse_semantic_location_history_from_takeout(
                location_takeout,
                root_prefix="Takeout/Location History/Semantic Location History/",
                start_month=start_month,
                end_month=end_month,
            )
        else:
            location_records = defaultdict(int)
            semantic_place_visits = defaultdict(int)
            semantic_activity_segments = defaultdict(int)
            semantic_top_places = defaultdict(Counter)
            semantic_top_activities = defaultdict(Counter)

        gmail_takeout_path: str | None = None
        gmail_takeout = select_archive_with_member(
            takeouts,
            "Takeout/Mail/All mail Including Spam and Trash.mbox",
        )
        if gmail_takeout is None:
            gmail_counts = defaultdict(int)
            gmail_from_domains = defaultdict(Counter)
            gmail_subject_tokens = defaultdict(Counter)
        else:
            gmail_takeout_path = str(gmail_takeout.tar_path)
            gmail_counts, gmail_from_domains, gmail_subject_tokens = parse_gmail_headers_from_takeout_mbox(
                gmail_takeout,
                member_path="Takeout/Mail/All mail Including Spam and Trash.mbox",
                start_month=start_month,
                end_month=end_month,
            )

    return LifeTimelineTakeoutBundle(
        google_search_counts=dict(google_search_counts),
        google_search_tokens=dict(google_search_tokens),
        google_search_phrases=dict(google_search_phrases),
        youtube_watch_counts=dict(youtube_watch_counts),
        youtube_search_counts=dict(youtube_search_counts),
        youtube_search_tokens=dict(youtube_search_tokens),
        youtube_search_phrases=dict(youtube_search_phrases),
        youtube_video_titles=dict(youtube_video_titles),
        youtube_watch_history_counts=dict(youtube_watch_history_counts),
        youtube_watch_history_video_ids=dict(youtube_watch_history_video_ids),
        youtube_watch_history_titles=dict(youtube_watch_history_titles),
        youtube_watch_history_channels=dict(youtube_watch_history_channels),
        youtube_search_history_counts=dict(youtube_search_history_counts),
        youtube_search_history_tokens=dict(youtube_search_history_tokens),
        youtube_search_history_phrases=dict(youtube_search_history_phrases),
        chrome_counts=dict(chrome_counts),
        maps_counts=dict(maps_counts),
        maps_tokens=dict(maps_tokens),
        maps_phrases=dict(maps_phrases),
        image_search_counts=dict(image_search_counts),
        image_search_tokens=dict(image_search_tokens),
        image_search_phrases=dict(image_search_phrases),
        play_store_counts=dict(play_store_counts),
        play_store_tokens=dict(play_store_tokens),
        play_store_phrases=dict(play_store_phrases),
        video_search_counts=dict(video_search_counts),
        video_search_tokens=dict(video_search_tokens),
        video_search_phrases=dict(video_search_phrases),
        shopping_counts=dict(shopping_counts),
        shopping_tokens=dict(shopping_tokens),
        shopping_phrases=dict(shopping_phrases),
        travel_counts=dict(travel_counts),
        travel_tokens=dict(travel_tokens),
        travel_phrases=dict(travel_phrases),
        myactivity_other_counts=dict(myactivity_other_counts),
        chrome_history_counts=dict(chrome_history_counts),
        chrome_history_domains=dict(chrome_history_domains),
        chrome_history_reddit_subs=dict(chrome_history_reddit_subs),
        chrome_history_title_tokens=dict(chrome_history_title_tokens),
        location_records=dict(location_records),
        semantic_place_visits=dict(semantic_place_visits),
        semantic_activity_segments=dict(semantic_activity_segments),
        semantic_top_places=dict(semantic_top_places),
        semantic_top_activities=dict(semantic_top_activities),
        gmail_counts=dict(gmail_counts),
        gmail_from_domains=dict(gmail_from_domains),
        gmail_subject_tokens=dict(gmail_subject_tokens),
        location_takeout_path=location_takeout_path,
        gmail_takeout_path=gmail_takeout_path,
        chrome_history_takeout_path=chrome_history_takeout_path,
        youtube_video_texts_takeout_path=youtube_video_texts_takeout_path,
    )


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


def discover_seed_archives(root: Path) -> List[Path]:
    if not root.exists():
        return []
    seeds = sorted(root.glob("takeout*-001.tgz"))
    if seeds:
        return seeds
    return sorted(root.glob("takeout*.tgz"))


def resolve_archives(*, explicit_seeds: List[Path], root: Path) -> List[Path]:
    expanded_takeouts: List[Path] = []
    for seed in explicit_seeds or discover_seed_archives(root):
        expanded_takeouts.extend(expand_takeout_parts(seed))

    seen_takeouts: set[str] = set()
    takeout_paths: List[Path] = []
    for path in sorted(expanded_takeouts, key=lambda p: p.name):
        if not path.exists():
            continue
        key = str(path.resolve())
        if key in seen_takeouts:
            continue
        seen_takeouts.add(key)
        takeout_paths.append(path)
    return takeout_paths


def select_archive_with_member(takeouts: List[TarReader], member_path: str) -> TarReader | None:
    matching = [tar for tar in takeouts if tar.has_member(member_path)]
    if not matching:
        return None
    return max(matching, key=lambda tar: tar.member_size(member_path) or 0)


def load_youtube_oembed_cache(cache_path: Path) -> Dict[str, dict[str, Any]]:
    if not cache_path.exists():
        return {}
    out: Dict[str, dict[str, Any]] = {}
    with cache_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            video_id = payload.get("video_id")
            if not isinstance(video_id, str) or not video_id:
                continue
            out[video_id] = payload
    return out


def resolve_youtube_video_meta(
    video_id: str,
    *,
    takeout_titles: Dict[str, str],
    oembed_cache: Dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    title = takeout_titles.get(video_id)
    channel: str | None = None

    cached = oembed_cache.get(video_id)
    if isinstance(cached, dict) and cached.get("ok") is True:
        if not title:
            cached_title = cached.get("title")
            if isinstance(cached_title, str) and cached_title.strip():
                title = cached_title.strip()
        cached_author = cached.get("author_name")
        if isinstance(cached_author, str) and cached_author.strip():
            channel = cached_author.strip()
    return title, channel
