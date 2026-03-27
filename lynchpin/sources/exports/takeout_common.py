from __future__ import annotations

import re
from datetime import date, datetime
from email.header import decode_header, make_header
from html import unescape as html_unescape
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


def tokenize(text: str) -> list[str]:
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
    "ale",
    "bo",
    "byc",
    "co",
    "czy",
    "do",
    "dla",
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
            vid = (qs.get("v") or [None])[0]
            if vid:
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
