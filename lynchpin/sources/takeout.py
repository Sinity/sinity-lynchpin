from __future__ import annotations

import csv
import io
import json
import re
import tarfile
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape as html_unescape
from types import TracebackType
from pathlib import Path
from typing import Callable, IO
from ..core.parse import month_key as _month_key, in_month_range as _month_in_range
from urllib.parse import parse_qs, unquote, urlparse


__all__ = [
    "TarReader",
    "MyActivityEvent",
    "LifeTakeoutBundle",
    "normalize_domain",
    "tokenize",
    "tokenize_topic",
    "decode_mime_header",
    "normalize_myactivity_whitespace",
    "myactivity_timestamp_key",
    "canonicalize_myactivity_href",
    "extract_youtube_video_id",
    "month_from_myactivity_date",
    "expand_takeout_parts",
    "discover_seed_archives",
    "resolve_archives",
    "select_archive_with_member",
    "iter_myactivity_events",
    "parse_myactivity_from_takeouts",
    "normalize_myactivity_category_dir_name",
    "discover_myactivity_category_member_paths",
    "parse_myactivity_counts_from_takeouts_member_paths",
    "parse_myactivity_other_category_counts_from_takeouts",
    "parse_myactivity_text_events_from_takeouts",
    "load_youtube_video_titles_from_takeout",
    "parse_youtube_watch_history_from_takeouts",
    "load_youtube_oembed_cache",
    "resolve_youtube_video_meta",
    "summarize_youtube_watch_history_month",
    "phrase_topic_tokens",
    "parse_chrome_history_json_from_takeout",
    "parse_location_records_from_takeout",
    "parse_semantic_location_history_from_takeout",
    "parse_gmail_headers_from_takeout_mbox",
    "parse_life_takeouts",
]

# ---------------------------------------------------------------------------
# Constants (from takeout_common.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Utility functions (from takeout_common.py)
# ---------------------------------------------------------------------------








def normalize_domain(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[\w]+", text.lower(), flags=re.UNICODE)
    return [t for t in tokens if t]


_TOPIC_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "he", "her", "his", "i", "if", "in", "is", "it", "its", "me",
    "my", "not", "of", "on", "or", "our", "ours", "she", "so", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "was", "we",
    "were", "what", "when", "where", "which", "who", "why", "will", "with",
    "you", "your",
    "ale", "bo", "byc", "co", "czy", "do", "dla", "jak", "ja", "jest", "juz",
    "mnie", "na", "nie", "od", "o", "po", "sie", "sa", "ta", "tak", "to", "tu", "w", "we",
    "wy", "za", "ze",
}


def tokenize_topic(text: str) -> list[str]:
    out: list[str] = []
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


def extract_youtube_video_id(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = normalize_domain(parsed.netloc)
    if host.endswith("youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            values = qs.get("v")
            if values:
                vid = values[0]
                return vid
        if parsed.path.startswith("/shorts/"):
            parts = parsed.path.split("/")
            return parts[2] if len(parts) >= 3 else None
    if host == "youtu.be":
        return parsed.path.strip("/") or None
    return None


def month_from_myactivity_date(date_text: str) -> str | None:
    normalized = normalize_myactivity_whitespace(date_text)
    match = MYACTIVITY_DATE_RE.search(normalized)
    if not match:
        return None
    month_name, day, year = match.groups()
    month_i = MONTHS.get(month_name)
    if not month_i:
        return None
    return f"{int(year):04d}-{month_i:02d}"


# ---------------------------------------------------------------------------
# TarReader and archive utilities (from takeout_archives.py)
# ---------------------------------------------------------------------------


class TarReader:
    def __init__(self, tar_path: Path):
        self.tar_path = tar_path
        self._tf: tarfile.TarFile | None = None
        self._members: dict[str, tarfile.TarInfo] = {}

    def __enter__(self) -> "TarReader":
        self._tf = tarfile.open(self.tar_path)
        self._members = {member.name: member for member in self._tf.getmembers()}
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._tf is not None:
            self._tf.close()
        self._tf = None
        self._members = {}

    def open(self, member_path: str) -> IO[bytes] | None:
        if self._tf is None:
            raise RuntimeError("TarReader not opened (use as a context manager).")
        member = self._members.get(member_path)
        if member is None:
            return None
        return self._tf.extractfile(member)

    def read_text(self, member_path: str) -> str | None:
        fh = self.open(member_path)
        if fh is None:
            return None
        return fh.read().decode("utf-8", errors="replace")

    def iter_members(self) -> list[tarfile.TarInfo]:
        return list(self._members.values())

    def has_member(self, member_path: str) -> bool:
        return member_path in self._members

    def member_size(self, member_path: str) -> int | None:
        member = self._members.get(member_path)
        return member.size if member is not None else None


def expand_takeout_parts(path: Path) -> list[Path]:
    """Expand a `...-001.tgz` seed path into all sibling takeout parts."""
    if not path.exists():
        return []
    name = path.name
    if name.endswith(".tgz"):
        stem = name[:-4]
        prefix, _, part = stem.rpartition("-")
        if prefix and part.isdigit() and len(part) == 3:
            return [candidate for candidate in sorted(path.parent.glob(f"{prefix}-*.tgz")) if candidate.exists()]
    return [path]


def discover_seed_archives(root: Path) -> list[Path]:
    if not root.exists():
        return []
    seeds = sorted(root.glob("takeout*-001.tgz"))
    if seeds:
        return seeds
    return sorted(root.glob("takeout*.tgz"))


def resolve_archives(*, explicit_seeds: list[Path], root: Path) -> list[Path]:
    expanded_takeouts: list[Path] = []
    for seed in explicit_seeds or discover_seed_archives(root):
        expanded_takeouts.extend(expand_takeout_parts(seed))

    seen_takeouts: set[str] = set()
    takeout_paths: list[Path] = []
    for path in sorted(expanded_takeouts, key=lambda candidate: candidate.name):
        if not path.exists():
            continue
        key = str(path.resolve())
        if key in seen_takeouts:
            continue
        seen_takeouts.add(key)
        takeout_paths.append(path)
    return takeout_paths


def select_archive_with_member(takeouts: list[TarReader], member_path: str) -> TarReader | None:
    matching = [tar for tar in takeouts if tar.has_member(member_path)]
    if not matching:
        return None
    return max(matching, key=lambda tar: tar.member_size(member_path) or 0)


# ---------------------------------------------------------------------------
# MyActivity parsing (from takeout_myactivity.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MyActivityEvent:
    category: str
    month: str
    action: str
    href: str | None
    text: str
    date_text: str
    timestamp: str


def iter_myactivity_events(html: str, category: str) -> list[MyActivityEvent]:
    events: list[MyActivityEvent] = []
    for match in MYACTIVITY_EVENT_RE.finditer(html or ""):
        cell_html, date_text = match.groups()
        if not cell_html or not date_text:
            continue
        month = month_from_myactivity_date(date_text)
        if not month:
            continue
        timestamp = myactivity_timestamp_key(date_text)

        anchor = MYACTIVITY_ANCHOR_RE.search(cell_html)
        href: str | None = None
        text = normalize_myactivity_whitespace(cell_html)
        action = text
        if anchor:
            href = html_unescape(anchor.group(1)).strip()
            anchor_text = html_unescape(re.sub(r"<[^>]+>", "", anchor.group(2))).strip()
            action_prefix = html_unescape(cell_html[: anchor.start()]).replace("\u00a0", " ").strip()
            action = action_prefix or normalize_myactivity_whitespace(cell_html)
            text = anchor_text

        events.append(
            MyActivityEvent(
                category=category,
                month=month,
                action=action,
                href=href,
                text=text,
                date_text=date_text,
                timestamp=timestamp,
            )
        )
    return events


def parse_myactivity_from_takeouts(
    takeouts: list[TarReader],
    category: str,
    member_path: str,
    start_month: str,
    end_month: str,
    include_actions: tuple[str, ...] | None = None,
) -> tuple[dict[str, int], dict[str, Counter[str]], dict[str, Counter[str]]]:
    counts: dict[str, int] = defaultdict(int)
    per_month_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_phrases: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[tuple[str, str, str, str]] = set()

    for tar in takeouts:
        html = tar.read_text(member_path)
        if html is None:
            continue
        for event in iter_myactivity_events(html, category=category):
            if event.month > end_month:
                continue
            if event.month < start_month:
                break
            if include_actions is not None and not any(event.action.startswith(action) for action in include_actions):
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
    return (name or "").strip().replace(" _ ", " & ")


def discover_myactivity_category_member_paths(takeouts: list[TarReader]) -> dict[str, set[str]]:
    by_category: dict[str, set[str]] = defaultdict(set)
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
    takeouts: list[TarReader],
    category: str,
    member_paths: list[str],
    start_month: str,
    end_month: str,
    include_actions: tuple[str, ...] | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    seen: set[tuple[str, str, str, str]] = set()

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
                if include_actions is not None and not any(event.action.startswith(action) for action in include_actions):
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
    takeouts: list[TarReader],
    start_month: str,
    end_month: str,
    *,
    exclude_categories: set[str],
) -> dict[str, Counter[str]]:
    per_month: dict[str, Counter[str]] = defaultdict(Counter)
    member_paths_by_category = discover_myactivity_category_member_paths(takeouts)
    for display_category, member_paths in sorted(member_paths_by_category.items(), key=lambda item: item[0].lower()):
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
    takeouts: list[TarReader],
    category: str,
    member_path: str,
    start_month: str,
    end_month: str,
    *,
    include_actions: tuple[str, ...] | None = None,
) -> tuple[dict[str, int], dict[str, Counter[str]], dict[str, Counter[str]]]:
    counts: dict[str, int] = defaultdict(int)
    per_month_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_phrases: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[tuple[str, str, str, str]] = set()

    for tar in takeouts:
        html = tar.read_text(member_path)
        if html is None:
            continue
        for event in iter_myactivity_events(html, category=category):
            if event.month > end_month:
                continue
            if event.month < start_month:
                break
            if include_actions is not None and not any(event.action.startswith(action) for action in include_actions):
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


# ---------------------------------------------------------------------------
# YouTube parsing (from takeout_youtube.py)
# ---------------------------------------------------------------------------


def load_youtube_video_titles_from_takeout(takeout: TarReader, member_path: str) -> dict[str, str]:
    fh = takeout.open(member_path)
    if fh is None:
        return {}
    wrapper = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
    reader = csv.DictReader(wrapper)
    titles: dict[str, str] = {}
    for row in reader:
        video_id = (row.get("Video ID") or "").strip()
        title = (row.get("Video Title Text Segments 1") or "").strip()
        if not video_id or not title:
            continue
        titles.setdefault(video_id, title)
    return titles


def parse_youtube_watch_history_from_takeouts(
    takeouts: list[TarReader],
    start_month: str,
    end_month: str,
) -> tuple[dict[str, int], dict[str, Counter[str]], dict[str, Counter[str]], dict[str, Counter[str]]]:
    counts: dict[str, int] = defaultdict(int)
    per_month_video_ids: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_titles: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_channels: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[tuple[str, str, str]] = set()

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

    def iter_watch_history_rows(html: str) -> list[tuple[str, str, str, str | None, str | None, str | None]]:
        rows: list[tuple[str, str, str, str | None, str | None, str | None]] = []
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

            parts = [part for part in cell.split(MYACTIVITY_BR) if part and part.strip()]
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

            rows.append(
                (month, timestamp, action, (canonicalize_myactivity_href(href) if href else None), (title or None), channel)
            )
        return rows

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


def load_youtube_oembed_cache(cache_path: Path) -> dict[str, dict[str, object]]:
    if not cache_path.exists():
        return {}
    out: dict[str, dict[str, object]] = {}
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
    takeout_titles: dict[str, str],
    oembed_cache: dict[str, dict[str, object]],
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


def summarize_youtube_watch_history_month(
    video_ids: Counter[str],
    titles: Counter[str],
    channels: Counter[str],
    *,
    takeout_titles: dict[str, str],
    oembed_cache: dict[str, dict[str, object]],
    tokenize_text: Callable[[str], list[str]] = tokenize_topic,
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
            _, channel = resolve_youtube_video_meta(vid, takeout_titles=takeout_titles, oembed_cache=oembed_cache)
            if channel:
                resolved_channels[channel] += count

    if not resolved_titles:
        for vid, count in video_ids.items():
            if not isinstance(vid, str) or not vid:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                continue
            title, _ = resolve_youtube_video_meta(vid, takeout_titles=takeout_titles, oembed_cache=oembed_cache)
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
    tokenize_text: Callable[[str], list[str]] = tokenize_topic,
    limit: int = 200,
) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for phrase, count in phrases.most_common(limit):
        for tok in tokenize_text(phrase):
            tokens[tok] += count
    return tokens


# ---------------------------------------------------------------------------
# Google takeout parsing (from takeout_google.py)
# ---------------------------------------------------------------------------


def parse_chrome_history_json_from_takeout(
    takeout: TarReader,
    member_path: str,
    start_month: str,
    end_month: str,
) -> tuple[dict[str, int], dict[str, Counter[str]], dict[str, Counter[str]], dict[str, Counter[str]]]:
    counts: dict[str, int] = defaultdict(int)
    per_month_domains: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_reddit_subs: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_title_tokens: dict[str, Counter[str]] = defaultdict(Counter)

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
        month = _month_key(dt)
        if not _month_in_range(month, start_month, end_month):
            continue
        raw_url = item.get("url")
        raw_title = item.get("title")
        url = raw_url if isinstance(raw_url, str) else ""
        title = raw_title if isinstance(raw_title, str) else ""
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
    tar: TarReader,
    member_path: str,
    start_month: str,
    end_month: str,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    fh = tar.open(member_path)
    if fh is None:
        return counts
    for raw in fh:
        line = raw.decode("utf-8", errors="replace")
        if not line.startswith('    "timestamp": '):
            continue
        match = re.search(r'"timestamp":\s+"(\d{4}-\d{2}-\d{2})T', line)
        if not match:
            continue
        year, month_i, _ = (int(part) for part in match.group(1).split("-", 2))
        month = f"{year:04d}-{month_i:02d}"
        if not _month_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
    return counts


def parse_semantic_location_history_from_takeout(
    tar: TarReader,
    root_prefix: str,
    start_month: str,
    end_month: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, Counter[str]], dict[str, Counter[str]]]:
    place_visits: dict[str, int] = defaultdict(int)
    activity_segments: dict[str, int] = defaultdict(int)
    per_month_places: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_activities: dict[str, Counter[str]] = defaultdict(Counter)

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
        match = re.match(r"(\d{4})_([A-Z]+)\.json$", tail)
        if not match:
            continue
        year = int(match.group(1))
        month_name = match.group(2)
        month_i = month_name_map.get(month_name)
        if not month_i:
            continue
        month = f"{year:04d}-{month_i:02d}"
        if not _month_in_range(month, start_month, end_month):
            continue
        fh = tar.open(member.name)
        if fh is None:
            continue
        payload = json.loads(fh.read().decode("utf-8", errors="replace"))
        for obj in payload.get("timelineObjects") or []:
            if "placeVisit" in obj:
                place_visits[month] += 1
                location = obj["placeVisit"].get("location") or {}
                address = (location.get("address") or "").strip()
                if address:
                    per_month_places[month][address] += 1
            if "activitySegment" in obj:
                activity_segments[month] += 1
                activity_type = obj["activitySegment"].get("activityType")
                if activity_type:
                    per_month_activities[month][activity_type] += 1
    return place_visits, activity_segments, per_month_places, per_month_activities


def parse_gmail_headers_from_takeout_mbox(
    tar: TarReader,
    member_path: str,
    start_month: str,
    end_month: str,
) -> tuple[dict[str, int], dict[str, Counter[str]], dict[str, Counter[str]]]:
    counts: dict[str, int] = defaultdict(int)
    per_month_from_domains: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_subject_tokens: dict[str, Counter[str]] = defaultdict(Counter)

    fh = tar.open(member_path)
    if fh is None:
        return counts, per_month_from_domains, per_month_subject_tokens

    in_headers = False
    headers: dict[str, str] = {}
    current_key: str | None = None

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
        month = _month_key(dt)
        if not _month_in_range(month, start_month, end_month):
            headers = {}
            return
        counts[month] += 1

        from_raw = decode_mime_header(headers.get("from", ""))
        for domain in re.findall(r"@([A-Za-z0-9._-]+)", from_raw):
            per_month_from_domains[month][domain.lower()] += 1

        subject = decode_mime_header(headers.get("subject", ""))
        for tok in tokenize(subject):
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


# ---------------------------------------------------------------------------
# Life takeout bundle (from takeout_life.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifeTakeoutBundle:
    google_search_counts: dict[str, int]
    google_search_tokens: dict[str, Counter[str]]
    google_search_phrases: dict[str, Counter[str]]
    youtube_watch_counts: dict[str, int]
    youtube_search_counts: dict[str, int]
    youtube_search_tokens: dict[str, Counter[str]]
    youtube_search_phrases: dict[str, Counter[str]]
    youtube_video_titles: dict[str, str]
    youtube_watch_history_counts: dict[str, int]
    youtube_watch_history_video_ids: dict[str, Counter[str]]
    youtube_watch_history_titles: dict[str, Counter[str]]
    youtube_watch_history_channels: dict[str, Counter[str]]
    youtube_search_history_counts: dict[str, int]
    youtube_search_history_tokens: dict[str, Counter[str]]
    youtube_search_history_phrases: dict[str, Counter[str]]
    chrome_counts: dict[str, int]
    maps_counts: dict[str, int]
    maps_tokens: dict[str, Counter[str]]
    maps_phrases: dict[str, Counter[str]]
    image_search_counts: dict[str, int]
    image_search_tokens: dict[str, Counter[str]]
    image_search_phrases: dict[str, Counter[str]]
    play_store_counts: dict[str, int]
    play_store_tokens: dict[str, Counter[str]]
    play_store_phrases: dict[str, Counter[str]]
    video_search_counts: dict[str, int]
    video_search_tokens: dict[str, Counter[str]]
    video_search_phrases: dict[str, Counter[str]]
    shopping_counts: dict[str, int]
    shopping_tokens: dict[str, Counter[str]]
    shopping_phrases: dict[str, Counter[str]]
    travel_counts: dict[str, int]
    travel_tokens: dict[str, Counter[str]]
    travel_phrases: dict[str, Counter[str]]
    myactivity_other_counts: dict[str, Counter[str]]
    chrome_history_counts: dict[str, int]
    chrome_history_domains: dict[str, Counter[str]]
    chrome_history_reddit_subs: dict[str, Counter[str]]
    chrome_history_title_tokens: dict[str, Counter[str]]
    location_records: dict[str, int]
    semantic_place_visits: dict[str, int]
    semantic_activity_segments: dict[str, int]
    semantic_top_places: dict[str, Counter[str]]
    semantic_top_activities: dict[str, Counter[str]]
    gmail_counts: dict[str, int]
    gmail_from_domains: dict[str, Counter[str]]
    gmail_subject_tokens: dict[str, Counter[str]]
    location_takeout_path: str | None
    gmail_takeout_path: str | None
    chrome_history_takeout_path: str | None
    youtube_video_texts_takeout_path: str | None


def parse_life_takeouts(
    takeout_paths: list[Path],
    *,
    start_month: str,
    end_month: str,
) -> LifeTakeoutBundle:
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

        youtube_video_titles: dict[str, str] = {}
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
        gmail_counts: dict[str, int]
        gmail_from_domains: dict[str, Counter[str]]
        gmail_subject_tokens: dict[str, Counter[str]]
        if gmail_takeout is None:
            gmail_counts = {}
            gmail_from_domains = {}
            gmail_subject_tokens = {}
        else:
            gmail_takeout_path = str(gmail_takeout.tar_path)
            gmail_counts, gmail_from_domains, gmail_subject_tokens = parse_gmail_headers_from_takeout_mbox(
                gmail_takeout,
                member_path="Takeout/Mail/All mail Including Spam and Trash.mbox",
                start_month=start_month,
                end_month=end_month,
            )

    return LifeTakeoutBundle(
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
