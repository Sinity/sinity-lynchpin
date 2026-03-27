from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from .takeout_archives import TarReader
from .takeout_common import (
    decode_mime_header,
    month_key_from_dt,
    month_key_in_range,
    normalize_domain,
    tokenize,
    tokenize_topic,
)


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
        if not month_key_in_range(month, start_month, end_month):
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
        if not month_key_in_range(month, start_month, end_month):
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
        month = month_key_from_dt(dt)
        if not month_key_in_range(month, start_month, end_month):
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
