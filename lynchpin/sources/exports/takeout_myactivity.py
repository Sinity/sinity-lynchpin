from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from html import unescape as html_unescape

from .takeout_archives import TarReader
from .takeout_common import (
    MYACTIVITY_ANCHOR_RE,
    MYACTIVITY_EVENT_RE,
    canonicalize_myactivity_href,
    month_from_myactivity_date,
    myactivity_timestamp_key,
    normalize_myactivity_whitespace,
    tokenize,
    tokenize_topic,
)


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
