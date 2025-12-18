#!/usr/bin/env python3
"""Build monthly "life timeline" metrics from local personal telemetry sources.

This script is intentionally high-sensitivity: it touches finance/health exports, web
history, Takeout, and private comms metadata. It is meant to run locally only.

Primary output:
- data/derived/monthly_life_2020-04_to_2023-04.json (default range)
"""

from __future__ import annotations

import csv
import json
import re
import io
import tarfile
from contextlib import ExitStack
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import typer

app = typer.Typer(pretty_exceptions_show_locals=False)


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


def month_key_from_date(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_key_from_dt(dt: datetime) -> str:
    return month_key_from_date(dt.date())


def month_key_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month


def iter_months(start_month: str, end_month: str) -> Iterator[str]:
    year, month = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_i = (int(part) for part in end_month.split("-", 1))
    while (year, month) <= (end_year, end_month_i):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month == 13:
            month = 1
            year += 1


def normalize_domain(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc


def safe_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def parse_pln_amount(text: str) -> Optional[float]:
    """Parse amounts like '-29,64 PLN' or '2 736,85 PLN' into a float."""
    cleaned = text.strip().replace("PLN", "").replace("\u00a0", " ").strip()
    cleaned = cleaned.replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned:
        return None
    return safe_float(cleaned)


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
    "być",
    "co",
    "czy",
    "do",
    "dla",
    "i",
    "jak",
    "ja",
    "jest",
    "już",
    "mnie",
    "na",
    "nie",
    "od",
    "o",
    "po",
    "się",
    "są",
    "ta",
    "tak",
    "to",
    "tu",
    "w",
    "we",
    "wy",
    "za",
    "że",
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


def parse_reddit_csv(
    path: Path,
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_subs: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            dt = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S %Z")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            counts[month] += 1
            per_month_subs[month][row.get("subreddit", "").strip() or "<unknown>"] += 1
    return counts, per_month_subs


def parse_reddit_comment_topic_tokens(path: Path, start_month: str, end_month: str) -> Dict[str, Counter[str]]:
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            dt = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S %Z")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            body = (row.get("body") or "").strip()
            if not body:
                continue
            for tok in tokenize_topic(body):
                per_month_tokens[month][tok] += 1
    return per_month_tokens


def parse_reddit_official_csv(path: Path, start_month: str, end_month: str) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw = (row.get("date") or "").strip()
            if not raw:
                continue
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %Z")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            counts[month] += 1
    return counts


def parse_wykop_link_comments(
    path: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_tags: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            dt = datetime.strptime(obj["comment_created_at"], "%Y-%m-%d %H:%M:%S")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            counts[month] += 1
            for tag in obj.get("link_tags") or []:
                per_month_tags[month][tag] += 1
    return counts, per_month_tags


def parse_wykop_entries(
    path: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_tags: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            dt = datetime.strptime(obj["entry_created_at"], "%Y-%m-%d %H:%M:%S")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            counts[month] += 1
            for tag in obj.get("entry_tags") or []:
                per_month_tags[month][tag] += 1
    return counts, per_month_tags


def parse_wykop_entry_comments(path: Path, start_month: str, end_month: str) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            dt = datetime.strptime(obj["comment_created_at"], "%Y-%m-%d %H:%M:%S")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            counts[month] += 1
    return counts


def parse_wykop_link_comment_topic_tokens(path: Path, start_month: str, end_month: str) -> Dict[str, Counter[str]]:
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            dt = datetime.strptime(obj["comment_created_at"], "%Y-%m-%d %H:%M:%S")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            content = (obj.get("comment_content") or "").strip()
            if not content:
                continue
            for tok in tokenize_topic(content):
                per_month_tokens[month][tok] += 1
    return per_month_tokens


def parse_wykop_entry_topic_tokens(path: Path, start_month: str, end_month: str) -> Dict[str, Counter[str]]:
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            dt = datetime.strptime(obj["entry_created_at"], "%Y-%m-%d %H:%M:%S")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            content = (obj.get("entry_content") or "").strip()
            if not content:
                continue
            for tok in tokenize_topic(content):
                per_month_tokens[month][tok] += 1
    return per_month_tokens


def parse_wykop_entry_comment_topic_tokens(path: Path, start_month: str, end_month: str) -> Dict[str, Counter[str]]:
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            dt = datetime.strptime(obj["comment_created_at"], "%Y-%m-%d %H:%M:%S")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            content = (obj.get("comment_content") or "").strip()
            if not content:
                continue
            for tok in tokenize_topic(content):
                per_month_tokens[month][tok] += 1
    return per_month_tokens


def parse_webhistory_ndjson(
    path: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_domains: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_reddit_subs: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_title_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            dt = datetime.fromisoformat(obj["iso_time"])
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            url = obj.get("url") or ""
            title = obj.get("title") or ""
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


def parse_raindrop_bookmarks(path: Path, start_month: str, end_month: str) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            created = (row.get("created") or "").strip()
            if not created:
                continue
            # 2025-08-17T13:19:33.851Z
            if created.endswith("Z"):
                created = created[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(created)
            except ValueError:
                continue
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            counts[month] += 1
    return counts


def _parse_goodreads_date(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_goodreads_library(
    path: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    read_counts: Dict[str, int] = defaultdict(int)
    added_counts: Dict[str, int] = defaultdict(int)
    per_month_authors_read: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_titles_read: Dict[str, Counter[str]] = defaultdict(Counter)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            title = (row.get("Title") or "").strip()
            author = (row.get("Author") or "").strip()

            dt_read = _parse_goodreads_date(row.get("Date Read") or "")
            if dt_read is not None:
                month = month_key_from_dt(dt_read)
                if month_key_in_range(month, start_month, end_month):
                    read_counts[month] += 1
                    if author:
                        per_month_authors_read[month][author] += 1
                    if title:
                        per_month_titles_read[month][title] += 1

            dt_added = _parse_goodreads_date(row.get("Date Added") or "")
            if dt_added is not None:
                month = month_key_from_dt(dt_added)
                if month_key_in_range(month, start_month, end_month):
                    added_counts[month] += 1

    return read_counts, added_counts, per_month_authors_read, per_month_titles_read


def parse_spotify_streaming(
    directory: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, float], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    hours: Dict[str, float] = defaultdict(float)
    per_month_artists: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tracks: Dict[str, Counter[str]] = defaultdict(Counter)
    for path in sorted(directory.glob("StreamingHistory*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload:
            dt = datetime.strptime(row["endTime"], "%Y-%m-%d %H:%M")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            ms_played = int(row.get("msPlayed") or 0)
            hours[month] += ms_played / 3_600_000
            artist = (row.get("artistName") or "").strip()
            track = (row.get("trackName") or "").strip()
            if artist:
                per_month_artists[month][artist] += ms_played
            if track:
                per_month_tracks[month][track] += ms_played
    return hours, per_month_artists, per_month_tracks


def parse_ledger_expenses(path: Path, start_month: str, end_month: str) -> Dict[str, float]:
    """Very small ledger parser: sum PLN expenses (Expenses:*) per month."""
    totals: Dict[str, float] = defaultdict(float)
    current_month: Optional[str] = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}\b", line):
                dt = datetime.strptime(line.split(" ", 1)[0], "%Y-%m-%d")
                current_month = month_key_from_dt(dt)
                continue
            if current_month is None:
                continue
            if not month_key_in_range(current_month, start_month, end_month):
                continue
            # Posting: <acct> <amount> PLN
            if "Expenses:" not in line:
                continue
            m = re.search(r"([+-]?\d[\d.,]*)\s+PLN\b", line)
            if not m:
                continue
            amount = m.group(1).replace(",", ".")
            value = safe_float(amount)
            if value is None:
                continue
            totals[current_month] += value
    return totals


def parse_revolut_statement(path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    out_pln: Dict[str, float] = defaultdict(float)
    in_pln: Dict[str, float] = defaultdict(float)
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if (row.get("Currency") or "").strip() != "PLN":
                continue
            raw_date = (row.get("Started Date") or "").strip()
            if not raw_date:
                continue
            dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            amount = safe_float((row.get("Amount") or "").strip())
            if amount is None:
                continue
            if amount < 0:
                out_pln[month] += abs(amount)
            elif amount > 0:
                in_pln[month] += amount
    return out_pln, in_pln


def parse_mbank_operations(path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Parse mBank 'lista operacji' CSV export (semicolon-separated; Polish formatting)."""
    out_pln: Dict[str, float] = defaultdict(float)
    in_pln: Dict[str, float] = defaultdict(float)
    in_table = False
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not in_table:
                if line.startswith("#Data operacji;"):
                    in_table = True
                continue
            if not line.strip():
                continue
            if not re.match(r"^\d{4}-\d{2}-\d{2};", line):
                continue
            parts = list(csv.reader([line], delimiter=";", quotechar='"'))[0]
            if not parts:
                continue
            dt = datetime.strptime(parts[0], "%Y-%m-%d")
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            # Kwota is field 4 in the exported table.
            if len(parts) < 5:
                continue
            amount = parse_pln_amount(parts[4])
            if amount is None:
                continue
            if amount < 0:
                out_pln[month] += abs(amount)
            elif amount > 0:
                in_pln[month] += amount
    return out_pln, in_pln


def parse_samsung_health_sleep(tar_path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, int], Dict[str, float]]:
    sessions: Dict[str, int] = defaultdict(int)
    total_hours: Dict[str, float] = defaultdict(float)
    with tarfile.open(tar_path) as tf:
        member = tf.getmember("samsunghealth_ezo.dev_20240813122209/com.samsung.shealth.sleep.20240813122209.csv")
        fh = tf.extractfile(member)
        if fh is None:
            return sessions, total_hours
        # Skip the first metadata line, then parse CSV with a header.
        first = fh.readline()
        if not first:
            return sessions, total_hours
        header = fh.readline()
        if not header:
            return sessions, total_hours
        columns = header.decode("utf-8", errors="replace").strip("\n").split(",")
        idx_start = columns.index("com.samsung.health.sleep.start_time")
        idx_duration = columns.index("sleep_duration")
        for raw in fh:
            row = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not row:
                continue
            parts = row.split(",")
            if len(parts) <= max(idx_start, idx_duration):
                continue
            start_raw = parts[idx_start].strip()
            duration_raw = parts[idx_duration].strip()
            if not start_raw or not duration_raw:
                continue
            try:
                dt = datetime.strptime(start_raw, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                continue
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            minutes = safe_float(duration_raw)
            if minutes is None or minutes <= 0:
                continue
            sessions[month] += 1
            total_hours[month] += minutes / 60.0
    return sessions, total_hours


def parse_samsung_health_weight(tar_path: Path, start_month: str, end_month: str) -> Dict[str, List[float]]:
    weights: Dict[str, List[float]] = defaultdict(list)
    with tarfile.open(tar_path) as tf:
        member = tf.getmember("samsunghealth_ezo.dev_20240813122209/com.samsung.health.weight.20240813122209.csv")
        fh = tf.extractfile(member)
        if fh is None:
            return weights
        first = fh.readline()
        if not first:
            return weights
        header = fh.readline()
        if not header:
            return weights
        columns = header.decode("utf-8", errors="replace").strip("\n").split(",")
        idx_time = columns.index("start_time")
        idx_weight = columns.index("weight")
        for raw in fh:
            row = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not row:
                continue
            parts = row.split(",")
            if len(parts) <= max(idx_time, idx_weight):
                continue
            time_raw = parts[idx_time].strip()
            weight_raw = parts[idx_weight].strip()
            if not time_raw or not weight_raw:
                continue
            try:
                dt = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                continue
            month = month_key_from_dt(dt)
            if not month_key_in_range(month, start_month, end_month):
                continue
            weight = safe_float(weight_raw)
            if weight is None:
                continue
            weights[month].append(weight)
    return weights


def parse_onenote_journal_entries(path: Path, start_month: str, end_month: str) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    header_re = re.compile(r"^###\s+(\d{2})\.(\d{2})\.(\d{4})")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            m = header_re.match(line.strip())
            if not m:
                continue
            day, month_i, year = (int(part) for part in m.groups())
            month = f"{year:04d}-{month_i:02d}"
            if not month_key_in_range(month, start_month, end_month):
                continue
            counts[month] += 1
    return counts


def parse_substance_log_headings(path: Path, start_month: str, end_month: str) -> Dict[str, int]:
    """Count `####` headings by month overlap, based on dates inside the heading text."""
    counts: Dict[str, int] = defaultdict(int)
    # Examples:
    # #### 20.05.2022 to 24.07.2022 - ...
    # #### 23.10.2022
    heading_re = re.compile(r"^####\s+(\d{2}\.\d{2}\.\d{4})(?:\s+to\s+(\d{2}\.\d{2}\.\d{4}))?")

    def parse_d(s: str) -> date:
        return datetime.strptime(s, "%d.%m.%Y").date()

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            m = heading_re.match(line.strip())
            if not m:
                continue
            start = parse_d(m.group(1))
            end = parse_d(m.group(2)) if m.group(2) else start
            start_m = month_key_from_date(start.replace(day=1))
            end_m = month_key_from_date(end.replace(day=1))
            for month in iter_months(start_m, end_m):
                if month_key_in_range(month, start_month, end_month):
                    counts[month] += 1
    return counts


@dataclass(frozen=True)
class MyActivityEvent:
    category: str
    month: str
    action: str
    href: str
    text: str
    date_text: str
    timestamp: str


def normalize_myactivity_whitespace(text: str) -> str:
    return (
        text.replace("\u202f", " ")
        .replace("\u00a0", " ")
        .replace("\u2009", " ")
        .replace("\u2028", " ")
        .strip()
    )


def myactivity_timestamp_key(date_text: str) -> str:
    normalized = normalize_myactivity_whitespace(date_text)
    match = MYACTIVITY_FULL_DT_RE.search(normalized)
    if not match:
        return normalized
    mon, day, year, hour, minute, second, ampm = match.groups()
    month_i = MONTHS.get(mon)
    if not month_i:
        return normalized
    hour_i = int(hour)
    if ampm.upper() == "AM":
        if hour_i == 12:
            hour_i = 0
    else:
        if hour_i != 12:
            hour_i += 12
    return f"{int(year):04d}-{month_i:02d}-{int(day):02d}T{hour_i:02d}:{int(minute):02d}:{int(second):02d}"


def canonicalize_myactivity_href(href: str) -> str:
    href = href.strip()
    if not href:
        return href
    parsed = urlparse(href)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        q = parse_qs(parsed.query).get("q")
        if q:
            return unquote(q[0])
    return href


def month_from_myactivity_date(date_text: str) -> Optional[str]:
    normalized = normalize_myactivity_whitespace(date_text)
    match = MYACTIVITY_DATE_RE.search(normalized)
    if not match:
        return None
    month_name, _, year = match.groups()
    month_i = MONTHS.get(month_name)
    if not month_i:
        return None
    return f"{int(year):04d}-{month_i:02d}"


def iter_myactivity_events(html: str, category: str) -> Iterator[MyActivityEvent]:
    for match in MYACTIVITY_EVENT_RE.finditer(html):
        content_html = match.group(1)
        date_text = html_unescape(match.group(2)).strip()
        month = month_from_myactivity_date(date_text)
        if not month:
            continue
        timestamp = myactivity_timestamp_key(date_text)

        anchor = MYACTIVITY_ANCHOR_RE.search(content_html)
        href = ""
        text = ""
        if anchor:
            href = html_unescape(anchor.group(1))
            text = html_unescape(re.sub(r"<[^>]+>", "", anchor.group(2))).strip()
        prefix = html_unescape(content_html.split("<a", 1)[0])
        action = prefix.replace("\u00a0", " ").strip()
        if not action:
            # Fallback: strip tags to get something stable.
            action = html_unescape(re.sub(r"<[^>]+>", "", content_html)).strip()
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
    """Return counts + token/phrase counters for query-like events.

    - counts: total (deduped) events per month for this category (after filtering by action, if provided)
    - tokens: per-month token counts for event text (only for 'Searched for' events)
    - phrases: per-month exact-text counts for event text (only for 'Searched for' events)
    """
    counts: Dict[str, int] = defaultdict(int)
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_phrases: Dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[Tuple[str, str, str, str]] = set()

    for tar in takeouts:
        html = tar.read_text(member_path)
        if html is None:
            continue
        for event in iter_myactivity_events(html, category=category):
            if not month_key_in_range(event.month, start_month, end_month):
                continue
            if include_actions is not None and not any(event.action.startswith(a) for a in include_actions):
                continue
            if event.action.startswith("Searched for"):
                content_key = event.text
            else:
                content_key = canonicalize_myactivity_href(event.href) or event.text
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


def parse_location_records_from_takeout(
    tar: TarReader, member_path: str, start_month: str, end_month: str
) -> Dict[str, int]:
    """Stream-parse Location History Records.json counts by month.

    This intentionally only counts *top-level* location objects by matching the 4-space-indented
    `"timestamp": "..."` lines, which correspond to location entries (nested activity timestamps
    are more indented).
    """
    counts: Dict[str, int] = defaultdict(int)
    fh = tar.open(member_path)
    if fh is None:
        return counts
    for raw in fh:
        line = raw.decode("utf-8", errors="replace")
        if not line.startswith('    "timestamp": '):
            continue
        #     "timestamp": "2014-04-24T14:11:16.787Z"
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

    month_name_map = {name.upper(): idx for name, idx in ((k, v) for k, v in MONTHS.items())}

    for member in tar.iter_members():
        if not member.isfile():
            continue
        if not member.name.startswith(root_prefix):
            continue
        if not member.name.endswith(".json"):
            continue
        # .../<year>/<year>_<MONTH>.json
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
    """Stream-parse Gmail mbox and extract (month -> count/from-domains/subject-tokens)."""
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
        # Crude domain extraction: grab anything after '@' until whitespace/'>'/'"'.
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
            # Header section ended.
            in_headers = False
            current_key = None
            continue
        if line[0] in {" ", "\t"} and current_key:
            # Folded header continuation.
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


@app.command()
def build(
    start: str = typer.Option("2020-04", help="Start month (YYYY-MM)"),
    end: str = typer.Option("2023-04", help="End month (YYYY-MM)"),
    output: Path = typer.Option(
        Path("data/derived/monthly_life_2020-04_to_2023-04.json"),
        help="Output JSON path (relative to repo root unless absolute).",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        help="Optional Markdown summary output (human-readable drilldown).",
    ),
    wykop_link_comments: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/wykop/Sinity/wykop_links_commented.jsonl"),
        help="Wykop commented links JSONL (canonical export).",
    ),
    wykop_entries: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/wykop/Sinity/wykop_entries_added.jsonl"),
        help="Wykop authored entries JSONL (canonical export).",
    ),
    wykop_entry_comments: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/wykop/Sinity/wykop_entry_comments.jsonl"),
        help="Wykop entry comments JSONL (canonical export).",
    ),
    reddit_comments: Path = typer.Option(
        Path("/realm/data/reddit_comments/reddit_comments.csv"),
        help="Reddit comments CSV (canonical).",
    ),
    reddit_posts: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/reddit/2025-12-14/posts.csv"),
        help="Reddit official export posts.csv",
    ),
    reddit_messages: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/reddit/2025-12-14/messages_archive_headers.csv"),
        help="Reddit official export message headers CSV.",
    ),
    webhistory: Path = typer.Option(
        Path("/realm/data/webhistory/manual_merge_output/full_history.ndjson"),
        help="Merged webhistory NDJSON (canonical).",
    ),
    raindrop_bookmarks: Path = typer.Option(
        Path("/realm/data/raindrop/raindrop_bookmarks_19_08_2025.csv"),
        help="Raindrop bookmarks CSV export.",
    ),
    goodreads_library: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/goodreads_library_export.csv"),
        help="Goodreads library export CSV.",
    ),
    spotify_dir: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/spotify/MyData"),
        help="Spotify MyData directory containing StreamingHistory*.json",
    ),
    ledger: Path = typer.Option(
        Path("/realm/data/finance/journal_clean"),
        help="Ledger file (ledger-cli/hledger-style).",
    ),
    revolut_legacy: Path = typer.Option(
        Path("/realm/data/finance/data/statements/revolut_ANNOTATED_PLN_statement_2019_09_01_2022_05_01.csv"),
        help="Revolut statement (legacy range, annotated).",
    ),
    revolut_new: Path = typer.Option(
        Path("/realm/data/finance/data/statements/newest/REVOLUT_PLN_account-statement_2022-10-02_2023-02-22_en-us_cea3dc.csv"),
        help="Revolut statement (newer range).",
    ),
    mbank_personal: Path = typer.Option(
        Path("/realm/data/finance/data/statements/newest/mbank_personal_lista_operacji_220222_230222_202302220823535351.csv"),
        help="mBank personal operations CSV (export).",
    ),
    mbank_business: Path = typer.Option(
        Path("/realm/data/finance/data/statements/newest/mbank_business_lista_operacji_220222_230222_202302220825097527.csv"),
        help="mBank business operations CSV (export).",
    ),
    samsung_health_tar: Path = typer.Option(
        Path("/realm/data/personal-data/samsunghealth.tar"),
        help="Samsung Health export tar.",
    ),
    onenote_journal: Path = typer.Option(
        Path("/realm/knowledgebase/logs.log-journal-onenote-2020.md"),
        help="OneNote journal export markdown.",
    ),
    substance_log: Path = typer.Option(
        Path("/realm/knowledgebase/logs.log-substance.md"),
        help="Substance log markdown.",
    ),
    takeout_20220516: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/google/takeout-20220516T172528Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20220716: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/google/takeout-20220716T171553Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20220916: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/google/takeout-20220916T171704Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20221116: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/google/takeout-20221116T172238Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20250124: Path = typer.Option(
        Path("/realm/data/personal-data/my_external_exports/google/takeout-20250124T064512Z-001.tgz"),
        help="Google Takeout tgz (2021-03 → 2025-01 coverage for My Activity + Gmail mbox).",
    ),
) -> None:
    start_month = start
    end_month = end

    months = list(iter_months(start_month, end_month))

    reddit_comment_counts, reddit_comment_subs = parse_reddit_csv(reddit_comments, start_month, end_month)
    reddit_comment_tokens = parse_reddit_comment_topic_tokens(reddit_comments, start_month, end_month)
    reddit_post_counts = parse_reddit_official_csv(reddit_posts, start_month, end_month)
    reddit_message_counts = parse_reddit_official_csv(reddit_messages, start_month, end_month)

    wykop_link_counts, wykop_link_tags = parse_wykop_link_comments(wykop_link_comments, start_month, end_month)
    wykop_link_tokens = parse_wykop_link_comment_topic_tokens(wykop_link_comments, start_month, end_month)
    wykop_entry_counts, wykop_entry_tags = parse_wykop_entries(wykop_entries, start_month, end_month)
    wykop_entry_tokens = parse_wykop_entry_topic_tokens(wykop_entries, start_month, end_month)
    wykop_entry_comment_counts = parse_wykop_entry_comments(wykop_entry_comments, start_month, end_month)
    wykop_entry_comment_tokens = parse_wykop_entry_comment_topic_tokens(wykop_entry_comments, start_month, end_month)

    web_counts, web_domains, web_reddit_subs, web_title_tokens = parse_webhistory_ndjson(webhistory, start_month, end_month)

    raindrop_counts = parse_raindrop_bookmarks(raindrop_bookmarks, start_month, end_month)
    goodreads_read_counts, goodreads_added_counts, goodreads_authors_read, goodreads_titles_read = parse_goodreads_library(
        goodreads_library, start_month, end_month
    )
    spotify_hours, spotify_artists, spotify_tracks = parse_spotify_streaming(spotify_dir, start_month, end_month)

    ledger_expenses = parse_ledger_expenses(ledger, start_month, end_month)
    revolut_out_legacy, revolut_in_legacy = parse_revolut_statement(revolut_legacy, start_month, end_month)
    revolut_out_new, revolut_in_new = parse_revolut_statement(revolut_new, start_month, end_month)

    mbank_personal_out, mbank_personal_in = parse_mbank_operations(mbank_personal, start_month, end_month)
    mbank_business_out, mbank_business_in = parse_mbank_operations(mbank_business, start_month, end_month)

    sleep_sessions, sleep_total_hours = parse_samsung_health_sleep(samsung_health_tar, start_month, end_month)
    weight_values = parse_samsung_health_weight(samsung_health_tar, start_month, end_month)

    onenote_counts = parse_onenote_journal_entries(onenote_journal, start_month, end_month)
    substance_headings = parse_substance_log_headings(substance_log, start_month, end_month)

    takeout_paths = [takeout_20220516, takeout_20220716, takeout_20220916, takeout_20221116, takeout_20250124]
    takeout_paths_used = [p for p in takeout_paths if p.exists()]
    if not takeout_paths_used:
        raise FileNotFoundError("No Google Takeout archives found (expected .tgz files under /realm/data/...).")

    location_takeout_path: str | None = None
    gmail_takeout_path: str | None = None

    with ExitStack() as stack:
        takeouts = [stack.enter_context(TarReader(path)) for path in takeout_paths_used]

        # My Activity: merge + dedupe across all takeouts that contain the member file.
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

        # Location History (heavy, but still manageable via streaming for records + per-month JSON for semantic)
        location_takeouts = [t for t in takeouts if t.has_member("Takeout/Location History/Records.json")]
        if location_takeouts:
            location_takeout = max(
                location_takeouts,
                key=lambda t: t.member_size("Takeout/Location History/Records.json") or 0,
            )
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

        gmail_takeout = next(
            (t for t in takeouts if t.has_member("Takeout/Mail/All mail Including Spam and Trash.mbox")),
            None,
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

    monthly: Dict[str, dict] = {}
    for month in months:
        sleep_total = sleep_total_hours.get(month, 0.0)
        sleep_n = sleep_sessions.get(month, 0)
        weights = weight_values.get(month, [])
        top_artists = [name for name, _ in spotify_artists.get(month, Counter()).most_common(3)]
        top_tracks = [name for name, _ in spotify_tracks.get(month, Counter()).most_common(3)]
        topic_tokens = Counter()
        topic_tokens.update(reddit_comment_tokens.get(month, Counter()))
        topic_tokens.update(wykop_link_tokens.get(month, Counter()))
        topic_tokens.update(wykop_entry_tokens.get(month, Counter()))
        topic_tokens.update(wykop_entry_comment_tokens.get(month, Counter()))
        monthly[month] = {
            "output": {
                "reddit_comments": reddit_comment_counts.get(month, 0),
                "reddit_posts": reddit_post_counts.get(month, 0),
                "reddit_messages": reddit_message_counts.get(month, 0),
                "wykop_link_comments": wykop_link_counts.get(month, 0),
                "wykop_entries": wykop_entry_counts.get(month, 0),
                "wykop_entry_comments": wykop_entry_comment_counts.get(month, 0),
                "reddit_top_subs": reddit_comment_subs.get(month, Counter()).most_common(15),
                "wykop_top_tags": wykop_link_tags.get(month, Counter()).most_common(15),
                "wykop_entries_top_tags": wykop_entry_tags.get(month, Counter()).most_common(15),
                "output_top_topic_tokens": topic_tokens.most_common(20),
            },
            "intake": {
                "webhistory_events": web_counts.get(month, 0),
                "webhistory_top_domains": web_domains.get(month, Counter()).most_common(15),
                "webhistory_top_reddit_subs": web_reddit_subs.get(month, Counter()).most_common(15),
                "webhistory_top_title_tokens": web_title_tokens.get(month, Counter()).most_common(15),
                "raindrop_bookmarks": raindrop_counts.get(month, 0),
                "goodreads_books_read": goodreads_read_counts.get(month, 0),
                "goodreads_books_added": goodreads_added_counts.get(month, 0),
                "goodreads_top_authors_read": goodreads_authors_read.get(month, Counter()).most_common(12),
                "goodreads_top_titles_read": goodreads_titles_read.get(month, Counter()).most_common(12),
                "google_searches": google_search_counts.get(month, 0),
                "google_search_top_tokens": google_search_tokens.get(month, Counter()).most_common(15),
                "google_search_top_queries": google_search_phrases.get(month, Counter()).most_common(15),
                "youtube_watch": youtube_watch_counts.get(month, 0),
                "youtube_searches": youtube_search_counts.get(month, 0),
                "youtube_search_top_tokens": youtube_search_tokens.get(month, Counter()).most_common(15),
                "youtube_search_top_queries": youtube_search_phrases.get(month, Counter()).most_common(15),
                "chrome_myactivity": chrome_counts.get(month, 0),
                "maps_myactivity": maps_counts.get(month, 0),
                "maps_search_top_tokens": maps_tokens.get(month, Counter()).most_common(15),
                "maps_search_top_queries": maps_phrases.get(month, Counter()).most_common(15),
                "image_search_myactivity": image_search_counts.get(month, 0),
                "image_search_top_tokens": image_search_tokens.get(month, Counter()).most_common(15),
                "image_search_top_queries": image_search_phrases.get(month, Counter()).most_common(15),
                "play_store_myactivity": play_store_counts.get(month, 0),
                "play_store_top_tokens": play_store_tokens.get(month, Counter()).most_common(15),
                "play_store_top_queries": play_store_phrases.get(month, Counter()).most_common(15),
                "video_search_myactivity": video_search_counts.get(month, 0),
                "video_search_top_tokens": video_search_tokens.get(month, Counter()).most_common(15),
                "video_search_top_queries": video_search_phrases.get(month, Counter()).most_common(15),
                "shopping_myactivity": shopping_counts.get(month, 0),
                "shopping_top_tokens": shopping_tokens.get(month, Counter()).most_common(15),
                "shopping_top_queries": shopping_phrases.get(month, Counter()).most_common(15),
                "travel_myactivity": travel_counts.get(month, 0),
                "travel_top_tokens": travel_tokens.get(month, Counter()).most_common(15),
                "travel_top_queries": travel_phrases.get(month, Counter()).most_common(15),
                "spotify_hours": round(spotify_hours.get(month, 0.0), 1) if month in spotify_hours else None,
                "spotify_top_artists": top_artists,
                "spotify_top_tracks": top_tracks,
            },
            "mail": {
                "gmail_messages": gmail_counts.get(month, 0),
                "gmail_top_from_domains": gmail_from_domains.get(month, Counter()).most_common(12),
                "gmail_top_subject_tokens": gmail_subject_tokens.get(month, Counter()).most_common(12),
            },
            "location": {
                "records": location_records.get(month, 0),
                "semantic_place_visits": semantic_place_visits.get(month, 0),
                "semantic_activity_segments": semantic_activity_segments.get(month, 0),
                "semantic_top_places": semantic_top_places.get(month, Counter()).most_common(12),
                "semantic_top_activities": semantic_top_activities.get(month, Counter()).most_common(12),
            },
            "money": {
                "ledger_expenses_pln": round(ledger_expenses.get(month, 0.0), 2) if month in ledger_expenses else None,
                "revolut_out_pln": round(revolut_out_legacy.get(month, 0.0) + revolut_out_new.get(month, 0.0), 2),
                "revolut_in_pln": round(revolut_in_legacy.get(month, 0.0) + revolut_in_new.get(month, 0.0), 2),
                "mbank_personal_out_pln": round(mbank_personal_out.get(month, 0.0), 2),
                "mbank_personal_in_pln": round(mbank_personal_in.get(month, 0.0), 2),
                "mbank_business_out_pln": round(mbank_business_out.get(month, 0.0), 2),
                "mbank_business_in_pln": round(mbank_business_in.get(month, 0.0), 2),
            },
            "health": {
                "sleep_sessions": sleep_n,
                "sleep_total_h": round(sleep_total, 2) if sleep_n else None,
                "sleep_avg_h": round(sleep_total / sleep_n, 2) if sleep_n else None,
                "weight_n": len(weights),
                "weight_min": min(weights) if weights else None,
                "weight_max": max(weights) if weights else None,
            },
            "notes": {
                "onenote_journal_entries": onenote_counts.get(month, 0),
                "substance_log_headings": substance_headings.get(month, 0),
            },
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "range": {"start_month": start_month, "end_month": end_month},
        "sources": {
            "reddit_comments_csv": str(reddit_comments),
            "wykop_link_comments_jsonl": str(wykop_link_comments),
            "wykop_entries_jsonl": str(wykop_entries),
            "wykop_entry_comments_jsonl": str(wykop_entry_comments),
            "webhistory_ndjson": str(webhistory),
            "google_takeouts": [str(p) for p in takeout_paths_used],
            "gmail_mbox": (
                f"{gmail_takeout_path}:{'Takeout/Mail/All mail Including Spam and Trash.mbox'}"
                if gmail_takeout_path
                else None
            ),
            "location_records": (
                f"{location_takeout_path}:{'Takeout/Location History/Records.json'}" if location_takeout_path else None
            ),
            "semantic_location_history": (
                f"{location_takeout_path}:Takeout/Location History/Semantic Location History/"
                if location_takeout_path
                else None
            ),
            "finance_ledger": str(ledger),
            "finance_revolut_legacy": str(revolut_legacy),
            "finance_revolut_new": str(revolut_new),
            "finance_mbank_personal": str(mbank_personal),
            "finance_mbank_business": str(mbank_business),
            "samsung_health_tar": str(samsung_health_tar),
            "onenote_journal": str(onenote_journal),
            "substance_log": str(substance_log),
            "raindrop_bookmarks": str(raindrop_bookmarks),
            "goodreads_library_csv": str(goodreads_library),
            "spotify_dir": str(spotify_dir),
        },
        "months": monthly,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.secho(f"Wrote {len(months)} months → {output}", fg=typer.colors.GREEN)

    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown(payload), encoding="utf-8")
        typer.secho(f"Wrote Markdown summary → {markdown_output}", fg=typer.colors.GREEN)


def _render_counter(counter: List[List[object]], limit: int = 12) -> str:
    items = []
    for key, value in counter[:limit]:
        items.append(f"{key} {value}")
    return ", ".join(items)


def render_markdown(payload: dict) -> str:
    generated_at = payload.get("generated_at", "<unknown>")
    months: Dict[str, dict] = payload.get("months") or {}
    start_month = (payload.get("range") or {}).get("start_month", "<unknown>")
    end_month = (payload.get("range") or {}).get("end_month", "<unknown>")

    lines: List[str] = []
    lines.append(f"# Life timeline auto-summary ({start_month} → {end_month})")
    lines.append("")
    lines.append(f"Generated: `{generated_at}`")
    lines.append(f"Backing JSON: `data/derived/monthly_life_2020-04_to_2023-04.json`")
    lines.append("")
    for month in sorted(months.keys()):
        m = months[month]
        out = m.get("output") or {}
        intake = m.get("intake") or {}
        mail = m.get("mail") or {}
        location = m.get("location") or {}
        money = m.get("money") or {}
        health = m.get("health") or {}
        notes = m.get("notes") or {}

        lines.append(f"## {month}")
        lines.append("")
        lines.append("**Snapshot**")
        lines.append("")
        lines.append(
            "- Output: "
            f"Reddit comments {out.get('reddit_comments', 0)}, posts {out.get('reddit_posts', 0)}, messages {out.get('reddit_messages', 0)}; "
            f"Wykop link-comments {out.get('wykop_link_comments', 0)}, entries {out.get('wykop_entries', 0)}, entry-comments {out.get('wykop_entry_comments', 0)}."
        )
        lines.append(
            "- Intake: "
            f"Google searches {intake.get('google_searches', 0)}; "
            f"YouTube watch {intake.get('youtube_watch', 0)}, YouTube searches {intake.get('youtube_searches', 0)}; "
            f"Webhistory events {intake.get('webhistory_events', 0)}; "
            f"Chrome MyActivity {intake.get('chrome_myactivity', 0)}; "
            f"Maps MyActivity {intake.get('maps_myactivity', 0)}, Image Search MyActivity {intake.get('image_search_myactivity', 0)}, "
            f"Play Store MyActivity {intake.get('play_store_myactivity', 0)}; "
            f"Video Search MyActivity {intake.get('video_search_myactivity', 0)}, Shopping MyActivity {intake.get('shopping_myactivity', 0)}, "
            f"Travel MyActivity {intake.get('travel_myactivity', 0)}; "
            f"Raindrop bookmarks {intake.get('raindrop_bookmarks', 0)}; "
            f"Goodreads read {intake.get('goodreads_books_read', 0)}, added {intake.get('goodreads_books_added', 0)}."
        )
        lines.append(
            "- Mail: "
            f"Gmail messages {mail.get('gmail_messages', 0)}."
        )
        lines.append(
            "- Location: "
            f"records {location.get('records', 0)}; "
            f"semantic place-visits {location.get('semantic_place_visits', 0)}, activity-segments {location.get('semantic_activity_segments', 0)}."
        )
        ledger_exp = money.get("ledger_expenses_pln")
        if ledger_exp is not None:
            lines.append(f"- Money: ledger expenses {ledger_exp} PLN.")
        lines.append(
            "- Money: "
            f"Revolut out {money.get('revolut_out_pln', 0)} / in {money.get('revolut_in_pln', 0)} PLN; "
            f"mBank personal out {money.get('mbank_personal_out_pln', 0)} / in {money.get('mbank_personal_in_pln', 0)} PLN; "
            f"mBank business out {money.get('mbank_business_out_pln', 0)} / in {money.get('mbank_business_in_pln', 0)} PLN."
        )
        if health.get("sleep_sessions"):
            lines.append(
                "- Health: "
                f"Sleep sessions {health.get('sleep_sessions')}; "
                f"avg {health.get('sleep_avg_h')} h; total {health.get('sleep_total_h')} h."
            )
        if health.get("weight_n"):
            lines.append(
                "- Health: "
                f"Weight {health.get('weight_min')}–{health.get('weight_max')} kg (n={health.get('weight_n')})."
            )
        lines.append(
            "- Notes: "
            f"OneNote journal entries {notes.get('onenote_journal_entries', 0)}; "
            f"substance log headings {notes.get('substance_log_headings', 0)}."
        )
        lines.append("")

        lines.append("**Output (top)**")
        lines.append("")
        lines.append(f"- Reddit top subs: {_render_counter(out.get('reddit_top_subs') or [])}")
        lines.append(f"- Wykop top tags: {_render_counter(out.get('wykop_top_tags') or [])}")
        lines.append(f"- Wykop entries top tags: {_render_counter(out.get('wykop_entries_top_tags') or [])}")
        lines.append(f"- Output topic tokens: {_render_counter(out.get('output_top_topic_tokens') or [])}")
        lines.append("")

        lines.append("**Intake (top)**")
        lines.append("")
        lines.append(f"- Webhistory top domains: {_render_counter(intake.get('webhistory_top_domains') or [])}")
        lines.append(f"- Webhistory top Reddit subs visited: {_render_counter(intake.get('webhistory_top_reddit_subs') or [])}")
        lines.append(f"- Webhistory title top tokens: {_render_counter(intake.get('webhistory_top_title_tokens') or [])}")
        lines.append(f"- Google search top tokens: {_render_counter(intake.get('google_search_top_tokens') or [])}")
        lines.append(f"- Google search top exact queries: {_render_counter(intake.get('google_search_top_queries') or [])}")
        lines.append(f"- YouTube search top tokens: {_render_counter(intake.get('youtube_search_top_tokens') or [])}")
        lines.append(f"- YouTube search top exact queries: {_render_counter(intake.get('youtube_search_top_queries') or [])}")
        lines.append(f"- Maps search top queries: {_render_counter(intake.get('maps_search_top_queries') or [])}")
        lines.append(f"- Video search top queries: {_render_counter(intake.get('video_search_top_queries') or [])}")
        lines.append(f"- Goodreads top authors read: {_render_counter(intake.get('goodreads_top_authors_read') or [])}")
        lines.append(f"- Goodreads top titles read: {_render_counter(intake.get('goodreads_top_titles_read') or [])}")
        spotify_h = intake.get("spotify_hours")
        if spotify_h:
            lines.append(f"- Spotify hours: {spotify_h} (top artists: {', '.join(intake.get('spotify_top_artists') or [])})")
        lines.append("")

        lines.append("**Mail (top)**")
        lines.append("")
        lines.append(f"- Gmail top from domains: {_render_counter(mail.get('gmail_top_from_domains') or [])}")
        lines.append(f"- Gmail top subject tokens: {_render_counter(mail.get('gmail_top_subject_tokens') or [])}")
        lines.append("")

        lines.append("**Location (top)**")
        lines.append("")
        lines.append(f"- Semantic top places: {_render_counter(location.get('semantic_top_places') or [])}")
        lines.append(f"- Semantic top activities: {_render_counter(location.get('semantic_top_activities') or [])}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    app()
