from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from html import unescape as html_unescape
from pathlib import Path
from urllib.parse import urlparse

from .takeout_archives import TarReader
from .takeout_common import (
    MYACTIVITY_ANCHOR_RE,
    MYACTIVITY_BR,
    MYACTIVITY_CONTENT_CELL_START,
    MYACTIVITY_DIV_END,
    canonicalize_myactivity_href,
    extract_youtube_video_id,
    month_from_myactivity_date,
    myactivity_timestamp_key,
    normalize_domain,
    tokenize_topic,
)


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
    tokenize_text=tokenize_topic,
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
    tokenize_text=tokenize_topic,
    limit: int = 200,
) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for phrase, count in phrases.most_common(limit):
        for tok in tokenize_text(phrase):
            tokens[tok] += count
    return tokens
