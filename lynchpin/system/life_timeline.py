#!/usr/bin/env python3
"""Build monthly "life timeline" metrics from local personal telemetry sources.

This script is intentionally high-sensitivity: it touches finance/health exports, web
history, Takeout, and private comms metadata. It is meant to run locally only.

Primary output:
- artefacts/lifelog/life-timeline/monthly_life_2020-04_to_2023-04.json (default range)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import re
import time
from contextlib import ExitStack, contextmanager
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import typer

from lynchpin.core.config import get_config
from lynchpin.sources.exports import raindrop as lp_raindrop
from lynchpin.sources.exports import reddit as lp_reddit
from lynchpin.sources.exports import spotify as lp_spotify
from lynchpin.sources.captures import webhistory as lp_webhistory
from lynchpin.sources.indices import gitstats as lp_gitstats
from lynchpin.sources.libraries import finance as lp_finance
from lynchpin.sources.exports import health as lp_health
from lynchpin.sources.exports import goodreads as lp_goodreads
from lynchpin.sources.exports import takeout as lp_takeout
from lynchpin.sources.exports import wykop as lp_wykop

app = typer.Typer(pretty_exceptions_show_locals=False)


@dataclass(frozen=True)
class LifeTimelineResult:
    output: Path
    start_month: str
    end_month: str
    month_count: int
    artifact_paths: Dict[str, Path]


def run_life_timeline(
    start: str = "2020-04",
    end: str = "2023-04",
    output: Path = Path("artefacts/lifelog/life-timeline/monthly_life_2020-04_to_2023-04.json"),
    markdown_output: Path | None = None,
    markdown_output_dir: Path | None = None,
) -> LifeTimelineResult:
    """Build a life timeline using repository defaults."""
    return build(
        start=start,
        end=end,
        output=output,
        markdown_output=markdown_output,
        markdown_output_dir=markdown_output_dir,
    )


@contextmanager
def _stage(label: str) -> Iterator[None]:
    start = time.monotonic()
    typer.echo(f"[life-timeline] {label}…", err=True)
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        typer.echo(f"[life-timeline] {label} done in {elapsed:.1f}s", err=True)


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


def summarize_reddit_messages(
    start_month: str,
    end_month: str,
    *,
    messages_path: Optional[Path],
) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    paths = [messages_path] if messages_path else None
    for message in lp_reddit.iter_message_headers(paths=paths):
        if message.created is None:
            continue
        month = month_key_from_dt(message.created)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
    return counts


def summarize_reddit_comments(
    start_month: str,
    end_month: str,
    *,
    comments_path: Optional[Path],
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_subs: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    paths = [comments_path] if comments_path else None
    for comment in lp_reddit.iter_comments(paths=paths):
        if comment.created is None:
            continue
        month = month_key_from_dt(comment.created)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
        per_month_subs[month][comment.subreddit.strip() or "<unknown>"] += 1
        if comment.body:
            for tok in tokenize_topic(comment.body):
                per_month_tokens[month][tok] += 1
    return counts, per_month_subs, per_month_tokens


def summarize_reddit_posts(
    start_month: str,
    end_month: str,
    *,
    posts_path: Optional[Path],
) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    paths = [posts_path] if posts_path else None
    for post in lp_reddit.iter_posts(paths=paths):
        if post.created is None:
            continue
        month = month_key_from_dt(post.created)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
    return counts


def summarize_wykop_link_comments(
    start_month: str,
    end_month: str,
    *,
    path: Optional[Path],
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_tags: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    for comment in lp_wykop.iter_link_comments(path=path):
        if comment.created_at is None:
            continue
        month = month_key_from_dt(comment.created_at)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
        for tag in comment.tags:
            per_month_tags[month][tag] += 1
        if comment.content:
            for tok in tokenize_topic(comment.content):
                per_month_tokens[month][tok] += 1
    return counts, per_month_tags, per_month_tokens


def summarize_wykop_entries(
    start_month: str,
    end_month: str,
    *,
    path: Optional[Path],
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_tags: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    for entry in lp_wykop.iter_entries(path=path):
        if entry.created_at is None:
            continue
        month = month_key_from_dt(entry.created_at)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
        for tag in entry.tags:
            per_month_tags[month][tag] += 1
        if entry.content:
            for tok in tokenize_topic(entry.content):
                per_month_tokens[month][tok] += 1
    return counts, per_month_tags, per_month_tokens


def summarize_wykop_entry_comments(
    start_month: str,
    end_month: str,
    *,
    path: Optional[Path],
) -> Tuple[Dict[str, int], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_tokens: Dict[str, Counter[str]] = defaultdict(Counter)
    for comment in lp_wykop.iter_entry_comments(path=path):
        if comment.created_at is None:
            continue
        month = month_key_from_dt(comment.created_at)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
        if comment.content:
            for tok in tokenize_topic(comment.content):
                per_month_tokens[month][tok] += 1
    return counts, per_month_tokens


def parse_webhistory_ndjson(
    path: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_webhistory.summarize_ndjson(path, start_month, end_month)


def parse_webhistory_gestalt_dir(
    root: Path, start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_webhistory.summarize_gestalt_dir(root, start_month, end_month)


def parse_chrome_history_json_from_takeout(
    takeout: "TarReader",
    member_path: str,
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_takeout.parse_chrome_history_json_from_takeout(
        takeout=takeout,
        member_path=member_path,
        start_month=start_month,
        end_month=end_month,
    )


def discover_git_repos() -> List[Path]:
    repos: List[Path] = []

    project_root = Path("/realm/project")
    if project_root.exists():
        for child in sorted(project_root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("_"):
                continue
            if (child / ".git").is_dir():
                repos.append(child)

    for extra in [Path("/realm/project/sinnix"), Path("/realm/project/knowledgebase")]:
        if (extra / ".git").is_dir():
            repos.append(extra)

    return repos


def parse_git_commit_activity(
    repos: List[Path], start_month: str, end_month: str
) -> Tuple[Dict[str, int], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_repos: Dict[str, Counter[str]] = defaultdict(Counter)
    for event in lp_gitstats.iter_commit_activity(repos, start_month=start_month, end_month=end_month):
        month = month_key_from_dt(event.timestamp)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
        per_month_repos[month][event.repo] += 1

    return counts, per_month_repos


def summarize_raindrop_bookmarks(
    start_month: str,
    end_month: str,
    *,
    csv_path: Optional[Path],
) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for bookmark in lp_raindrop.iter_bookmarks(csv_path):
        if bookmark.created is None:
            continue
        month = month_key_from_dt(bookmark.created)
        if not month_key_in_range(month, start_month, end_month):
            continue
        counts[month] += 1
    return counts


def summarize_spotify_streaming(
    start_month: str,
    end_month: str,
    *,
    root: Optional[Path],
) -> Tuple[Dict[str, float], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    hours: Dict[str, float] = defaultdict(float)
    per_month_artists: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_tracks: Dict[str, Counter[str]] = defaultdict(Counter)
    for stream in lp_spotify.iter_streams(root=root):
        if stream.end_time is None:
            continue
        month = month_key_from_dt(stream.end_time)
        if not month_key_in_range(month, start_month, end_month):
            continue
        hours[month] += stream.ms_played / 3_600_000
        if stream.artist:
            per_month_artists[month][stream.artist] += stream.ms_played
        if stream.track:
            per_month_tracks[month][stream.track] += stream.ms_played
    return hours, per_month_artists, per_month_tracks


def parse_ledger_expenses(path: Path, start_month: str, end_month: str) -> Dict[str, float]:
    return lp_finance.parse_ledger_expenses(path, start_month, end_month)


def parse_revolut_statement(path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    return lp_finance.parse_revolut_statement(path, start_month, end_month)


def parse_mbank_operations(path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    return lp_finance.parse_mbank_operations(path, start_month, end_month)


def parse_samsung_health_sleep(tar_path: Path, start_month: str, end_month: str) -> Tuple[Dict[str, int], Dict[str, float]]:
    return lp_health.parse_samsung_health_sleep(tar_path, start_month, end_month)


def parse_samsung_health_weight(tar_path: Path, start_month: str, end_month: str) -> Dict[str, List[float]]:
    return lp_health.parse_samsung_health_weight(tar_path, start_month, end_month)


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


TarReader = lp_takeout.TarReader


def parse_myactivity_from_takeouts(
    takeouts: List[TarReader],
    category: str,
    member_path: str,
    start_month: str,
    end_month: str,
    include_actions: Optional[Tuple[str, ...]] = None,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_takeout.parse_myactivity_from_takeouts(
        takeouts=takeouts,
        category=category,
        member_path=member_path,
        start_month=start_month,
        end_month=end_month,
        include_actions=include_actions,
    )


def discover_myactivity_category_member_paths(takeouts: List[TarReader]) -> Dict[str, set[str]]:
    return lp_takeout.discover_myactivity_category_member_paths(takeouts)


def parse_myactivity_counts_from_takeouts_member_paths(
    takeouts: List[TarReader],
    category: str,
    member_paths: List[str],
    start_month: str,
    end_month: str,
    include_actions: Optional[Tuple[str, ...]] = None,
) -> Dict[str, int]:
    return lp_takeout.parse_myactivity_counts_from_takeouts_member_paths(
        takeouts=takeouts,
        category=category,
        member_paths=member_paths,
        start_month=start_month,
        end_month=end_month,
        include_actions=include_actions,
    )


def parse_myactivity_other_category_counts_from_takeouts(
    takeouts: List[TarReader],
    start_month: str,
    end_month: str,
    *,
    exclude_categories: set[str],
) -> Dict[str, Counter[str]]:
    return lp_takeout.parse_myactivity_other_category_counts_from_takeouts(
        takeouts=takeouts,
        start_month=start_month,
        end_month=end_month,
        exclude_categories=exclude_categories,
    )


def parse_myactivity_text_events_from_takeouts(
    takeouts: List[TarReader],
    category: str,
    member_path: str,
    start_month: str,
    end_month: str,
    *,
    include_actions: Optional[Tuple[str, ...]] = None,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_takeout.parse_myactivity_text_events_from_takeouts(
        takeouts=takeouts,
        category=category,
        member_path=member_path,
        start_month=start_month,
        end_month=end_month,
        include_actions=include_actions,
    )


def load_youtube_video_titles_from_takeout(takeout: TarReader, member_path: str) -> Dict[str, str]:
    return lp_takeout.load_youtube_video_titles_from_takeout(takeout, member_path)


def parse_youtube_watch_history_from_takeouts(
    takeouts: List[TarReader],
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_takeout.parse_youtube_watch_history_from_takeouts(
        takeouts=takeouts,
        start_month=start_month,
        end_month=end_month,
    )


def _load_youtube_oembed_cache(cache_path: Path) -> Dict[str, dict[str, Any]]:
    """Load a local JSONL cache keyed by `video_id`.

    Expected rows are produced by `python -m lynchpin.system.life_timeline_oembed enrich`.
    """
    if not cache_path.exists():
        return {}
    out: Dict[str, dict[str, Any]] = {}
    with cache_path.open("r", encoding="utf-8") as fh:
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
            video_id = obj.get("video_id")
            if not isinstance(video_id, str) or not video_id:
                continue
            out[video_id] = obj
    return out


def _resolve_youtube_video_meta(
    video_id: str,
    *,
    takeout_titles: Dict[str, str],
    oembed_cache: Dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return (title, channel) for a YouTube video ID when possible."""
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


def parse_location_records_from_takeout(
    tar: TarReader, member_path: str, start_month: str, end_month: str
) -> Dict[str, int]:
    return lp_takeout.parse_location_records_from_takeout(
        tar=tar,
        member_path=member_path,
        start_month=start_month,
        end_month=end_month,
    )


def parse_semantic_location_history_from_takeout(
    tar: TarReader,
    root_prefix: str,
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_takeout.parse_semantic_location_history_from_takeout(
        tar=tar,
        root_prefix=root_prefix,
        start_month=start_month,
        end_month=end_month,
    )


def parse_gmail_headers_from_takeout_mbox(
    tar: TarReader,
    member_path: str,
    start_month: str,
    end_month: str,
) -> Tuple[Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    return lp_takeout.parse_gmail_headers_from_takeout_mbox(
        tar=tar,
        member_path=member_path,
        start_month=start_month,
        end_month=end_month,
    )


def expand_takeout_parts(path: Path) -> List[Path]:
    return lp_takeout.expand_takeout_parts(path)


@app.command()
def build(
    start: str = typer.Option("2020-04", help="Start month (YYYY-MM)"),
    end: str = typer.Option("2023-04", help="End month (YYYY-MM)"),
    output: Path = typer.Option(
        Path("artefacts/lifelog/life-timeline/monthly_life_2020-04_to_2023-04.json"),
        help="Output JSON path (relative to repo root unless absolute).",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        help="Optional Markdown summary output (human-readable drilldown).",
    ),
    markdown_output_dir: Optional[Path] = typer.Option(
        None,
        help="Optional directory to write per-year Markdown drilldowns (writes an index.md + YYYY.md files).",
    ),
    wykop_link_comments: Path = typer.Option(
        Path("/realm/data/exports/wykop/raw/Sinity/wykop_links_commented.jsonl"),
        help="Wykop commented links JSONL (canonical export).",
    ),
    wykop_entries: Path = typer.Option(
        Path("/realm/data/exports/wykop/raw/Sinity/wykop_entries_added.jsonl"),
        help="Wykop authored entries JSONL (canonical export).",
    ),
    wykop_entry_comments: Path = typer.Option(
        Path("/realm/data/exports/wykop/raw/Sinity/wykop_entry_comments.jsonl"),
        help="Wykop entry comments JSONL (canonical export).",
    ),
    reddit_comments: Optional[Path] = typer.Option(
        None,
        help="Optional Reddit comments CSV override (defaults to latest GDPR export).",
    ),
    reddit_posts: Optional[Path] = typer.Option(
        None,
        help="Optional Reddit posts CSV override (defaults to latest GDPR export).",
    ),
    reddit_messages: Optional[Path] = typer.Option(
        None,
        help="Optional Reddit message headers CSV override (defaults to latest GDPR export).",
    ),
    webhistory: Path = typer.Option(
        Path("/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson"),
        help="Canonical merged webhistory NDJSON (derived from raw).",
    ),
    webhistory_gestalt_dir: Optional[Path] = typer.Option(
        None,
        help="Optional deduped gestalt segments directory (legacy/debug input; overrides NDJSON when provided).",
    ),
    youtube_oembed_cache: Path = typer.Option(
        Path("artefacts/lifelog/life-timeline/youtube_oembed_cache.jsonl"),
        help="Optional JSONL cache for YouTube oEmbed lookups (video_id → title/channel).",
    ),
    raindrop_bookmarks: Path = typer.Option(
        Path("/realm/data/exports/raindrop/raw/raindrop_bookmarks_19_08_2025.csv"),
        help="Raindrop bookmarks CSV export.",
    ),
    goodreads_library: Path = typer.Option(
        Path("/realm/data/exports/goodreads/raw/library_export.csv"),
        help="Goodreads library export CSV.",
    ),
    spotify_dir: Optional[Path] = typer.Option(
        None,
        help="Spotify export root (defaults to lynchpin config when omitted).",
    ),
    ledger: Path = typer.Option(
        Path("/realm/data/libraries/finance/journal_clean"),
        help="Ledger file (ledger-cli/hledger-style).",
    ),
    revolut_legacy: Path = typer.Option(
        Path("/realm/data/libraries/finance/data/statements/revolut_ANNOTATED_PLN_statement_2019_09_01_2022_05_01.csv"),
        help="Revolut statement (legacy range, annotated).",
    ),
    revolut_new: Path = typer.Option(
        Path("/realm/data/libraries/finance/data/statements/newest/REVOLUT_PLN_account-statement_2022-10-02_2023-02-22_en-us_cea3dc.csv"),
        help="Revolut statement (newer range).",
    ),
    mbank_personal: Path = typer.Option(
        Path("/realm/data/libraries/finance/data/statements/newest/mbank_personal_lista_operacji_220222_230222_202302220823535351.csv"),
        help="mBank personal operations CSV (export).",
    ),
    mbank_business: Path = typer.Option(
        Path("/realm/data/libraries/finance/data/statements/newest/mbank_business_lista_operacji_220222_230222_202302220825097527.csv"),
        help="mBank business operations CSV (export).",
    ),
    samsung_health_export: Path = typer.Option(
        Path("/realm/data/exports/health/raw/samsung-health"),
        help="Samsung Health export directory or tar.",
    ),
    onenote_journal: Path = typer.Option(
        Path("/realm/project/knowledgebase/logs.log-journal-onenote-2020.md"),
        help="OneNote journal export markdown.",
    ),
    substance_log: Path = typer.Option(
        Path("/realm/project/knowledgebase/logs.log-substance.md"),
        help="Substance log markdown.",
    ),
    takeout_20220516: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20220516T172528Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20220716: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20220716T171553Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20220916: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20220916T171704Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20221116: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20221116T172238Z-001.tgz"),
        help="Google Takeout tgz (My Activity + Location History, part 1).",
    ),
    takeout_20250124: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20250124T064512Z-001.tgz"),
        help="Google Takeout tgz (2021-03 to 2025-01 coverage for My Activity + Gmail mbox).",
    ),
    takeout_20251214_part9: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20251214T223432Z-9-001.tgz"),
        help="Google Takeout tgz (2025-12; YouTube watch/search history HTML).",
    ),
    takeout_20251214_part11: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20251214T223432Z-11-001.tgz"),
        help="Google Takeout tgz (2025-12; Gmail mbox).",
    ),
    takeout_20251214_part15: Path = typer.Option(
        Path("/realm/data/exports/google/raw/takeout/takeout-20251214T223432Z-15-001.tgz"),
        help="Google Takeout tgz (2025-12; My Activity HTML + Chrome History JSON).",
    ),
) -> LifeTimelineResult:
    start_month = start
    end_month = end
    if markdown_output is not None and markdown_output_dir is not None:
        raise ValueError("Pass at most one of --markdown-output or --markdown-output-dir.")

    months = list(iter_months(start_month, end_month))

    with _stage("Parse Reddit"):
        (
            reddit_comment_counts,
            reddit_comment_subs,
            reddit_comment_tokens,
        ) = summarize_reddit_comments(
            start_month,
            end_month,
            comments_path=reddit_comments,
        )
        reddit_post_counts = summarize_reddit_posts(
            start_month,
            end_month,
            posts_path=reddit_posts,
        )
        reddit_message_counts = summarize_reddit_messages(
            start_month,
            end_month,
            messages_path=reddit_messages,
        )

    with _stage("Parse Wykop"):
        (
            wykop_link_counts,
            wykop_link_tags,
            wykop_link_tokens,
        ) = summarize_wykop_link_comments(
            start_month,
            end_month,
            path=wykop_link_comments,
        )
        (
            wykop_entry_counts,
            wykop_entry_tags,
            wykop_entry_tokens,
        ) = summarize_wykop_entries(
            start_month,
            end_month,
            path=wykop_entries,
        )
        (
            wykop_entry_comment_counts,
            wykop_entry_comment_tokens,
        ) = summarize_wykop_entry_comments(
            start_month,
            end_month,
            path=wykop_entry_comments,
        )

    webhistory_source: str
    with _stage("Parse webhistory"):
        if webhistory_gestalt_dir is not None and webhistory_gestalt_dir.exists():
            webhistory_source = "gestalt"
            web_counts, web_domains, web_reddit_subs, web_title_tokens = parse_webhistory_gestalt_dir(
                webhistory_gestalt_dir, start_month, end_month
            )
        else:
            webhistory_source = "ndjson"
            web_counts, web_domains, web_reddit_subs, web_title_tokens = parse_webhistory_ndjson(
                webhistory, start_month, end_month
            )

    resolved_spotify_dir = spotify_dir or get_config().spotify_root
    with _stage("Parse bookmarks/media"):
        raindrop_counts = summarize_raindrop_bookmarks(
            start_month,
            end_month,
            csv_path=raindrop_bookmarks,
        )
        (
            goodreads_read_counts,
            goodreads_added_counts,
            goodreads_authors_read,
            goodreads_titles_read,
        ) = lp_goodreads.summarize_library(
            start_month,
            end_month,
            path=goodreads_library,
        )
        spotify_hours, spotify_artists, spotify_tracks = summarize_spotify_streaming(
            start_month,
            end_month,
            root=resolved_spotify_dir,
        )

    with _stage("Parse finance"):
        ledger_expenses = parse_ledger_expenses(ledger, start_month, end_month)
        revolut_out_legacy, revolut_in_legacy = parse_revolut_statement(revolut_legacy, start_month, end_month)
        revolut_out_new, revolut_in_new = parse_revolut_statement(revolut_new, start_month, end_month)

        mbank_personal_out, mbank_personal_in = parse_mbank_operations(mbank_personal, start_month, end_month)
        mbank_business_out, mbank_business_in = parse_mbank_operations(mbank_business, start_month, end_month)

    with _stage("Parse health"):
        sleep_sessions, sleep_total_hours = parse_samsung_health_sleep(
            samsung_health_export,
            start_month,
            end_month,
        )
        weight_values = parse_samsung_health_weight(
            samsung_health_export,
            start_month,
            end_month,
        )

    with _stage("Parse notes"):
        onenote_counts = parse_onenote_journal_entries(onenote_journal, start_month, end_month)
        substance_headings = parse_substance_log_headings(substance_log, start_month, end_month)

    with _stage("Parse git activity"):
        git_repos = discover_git_repos()
        git_commit_counts, git_commit_repos = parse_git_commit_activity(git_repos, start_month, end_month)

    with _stage("Discover Google Takeout archives"):
        takeout_seed_paths = [
            takeout_20220516,
            takeout_20220716,
            takeout_20220916,
            takeout_20221116,
            takeout_20250124,
            takeout_20251214_part9,
            takeout_20251214_part11,
            takeout_20251214_part15,
        ]
        expanded_takeouts: List[Path] = []
        for seed in takeout_seed_paths:
            expanded_takeouts.extend(expand_takeout_parts(seed))
        seen_takeouts: set[str] = set()
        takeout_paths_used: List[Path] = []
        for path in sorted(expanded_takeouts, key=lambda p: p.name):
            if not path.exists():
                continue
            key = str(path.resolve())
            if key in seen_takeouts:
                continue
            seen_takeouts.add(key)
            takeout_paths_used.append(path)
        if not takeout_paths_used:
            raise FileNotFoundError(
                "No Google Takeout archives found (expected .tgz files under /realm/data/exports/google/...)."
            )

    location_takeout_path: str | None = None
    gmail_takeout_path: str | None = None
    chrome_history_takeout_path: str | None = None
    youtube_video_texts_takeout_path: str | None = None

    with _stage(f"Parse Google Takeout ({len(takeout_paths_used)} archives)"):
        with ExitStack() as stack:
            takeouts = [stack.enter_context(TarReader(path)) for path in takeout_paths_used]

            # My Activity: merge + dedupe across all takeouts that contain the member file.
            with _stage("Takeout: Search"):
                google_search_counts, google_search_tokens, google_search_phrases = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="search",
                    member_path="Takeout/My Activity/Search/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=("Searched for",),
                )
            with _stage("Takeout: YouTube (MyActivity watch/search)"):
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
            with _stage("Takeout: YouTube video metadata (video texts.csv)"):
                youtube_video_text_takeouts = [
                    t for t in takeouts if t.has_member("Takeout/YouTube and YouTube Music/video metadata/video texts.csv")
                ]
                if youtube_video_text_takeouts:
                    youtube_video_text_takeout = max(
                        youtube_video_text_takeouts,
                        key=lambda t: t.member_size("Takeout/YouTube and YouTube Music/video metadata/video texts.csv") or 0,
                    )
                    youtube_video_texts_takeout_path = str(youtube_video_text_takeout.tar_path)
                    youtube_video_titles = load_youtube_video_titles_from_takeout(
                        youtube_video_text_takeout,
                        member_path="Takeout/YouTube and YouTube Music/video metadata/video texts.csv",
                    )

            with _stage("Takeout: YouTube watch-history.html"):
                (
                    youtube_watch_history_counts,
                    youtube_watch_history_video_ids,
                    youtube_watch_history_titles,
                    youtube_watch_history_channels,
                ) = parse_youtube_watch_history_from_takeouts(takeouts=takeouts, start_month=start_month, end_month=end_month)
            with _stage("Takeout: YouTube search-history.html"):
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
            with _stage("Takeout: Chrome MyActivity"):
                chrome_counts, _, _ = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="chrome",
                    member_path="Takeout/My Activity/Chrome/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=None,
                )
            with _stage("Takeout: Maps MyActivity"):
                maps_counts, maps_tokens, maps_phrases = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="maps",
                    member_path="Takeout/My Activity/Maps/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=None,
                )
            with _stage("Takeout: Image Search MyActivity"):
                image_search_counts, image_search_tokens, image_search_phrases = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="image_search",
                    member_path="Takeout/My Activity/Image Search/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=None,
                )
            with _stage("Takeout: Play Store MyActivity"):
                play_store_counts, play_store_tokens, play_store_phrases = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="play_store",
                    member_path="Takeout/My Activity/Google Play Store/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=None,
                )
            with _stage("Takeout: Video Search MyActivity"):
                video_search_counts, video_search_tokens, video_search_phrases = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="video_search",
                    member_path="Takeout/My Activity/Video Search/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=None,
                )
            with _stage("Takeout: Shopping MyActivity"):
                shopping_counts, shopping_tokens, shopping_phrases = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="shopping",
                    member_path="Takeout/My Activity/Shopping/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=None,
                )
            with _stage("Takeout: Travel MyActivity"):
                travel_counts, travel_tokens, travel_phrases = parse_myactivity_from_takeouts(
                    takeouts=takeouts,
                    category="travel",
                    member_path="Takeout/My Activity/Travel/MyActivity.html",
                    start_month=start_month,
                    end_month=end_month,
                    include_actions=None,
                )

            with _stage("Takeout: Other MyActivity categories"):
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

            with _stage("Takeout: Chrome History.json"):
                chrome_history_takeouts = [t for t in takeouts if t.has_member("Takeout/Chrome/History.json")]
                if chrome_history_takeouts:
                    chrome_history_takeout = max(
                        chrome_history_takeouts,
                        key=lambda t: t.member_size("Takeout/Chrome/History.json") or 0,
                    )
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

            with _stage("Takeout: Location History"):
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

            with _stage("Takeout: Gmail mbox headers"):
                gmail_takeouts = [t for t in takeouts if t.has_member("Takeout/Mail/All mail Including Spam and Trash.mbox")]
                if not gmail_takeouts:
                    gmail_counts = defaultdict(int)
                    gmail_from_domains = defaultdict(Counter)
                    gmail_subject_tokens = defaultdict(Counter)
                else:
                    gmail_takeout = max(
                        gmail_takeouts,
                        key=lambda t: t.member_size("Takeout/Mail/All mail Including Spam and Trash.mbox") or 0,
                    )
                    gmail_takeout_path = str(gmail_takeout.tar_path)
                    gmail_counts, gmail_from_domains, gmail_subject_tokens = parse_gmail_headers_from_takeout_mbox(
                        gmail_takeout,
                        member_path="Takeout/Mail/All mail Including Spam and Trash.mbox",
                        start_month=start_month,
                        end_month=end_month,
                    )

    youtube_oembed_by_id = _load_youtube_oembed_cache(youtube_oembed_cache)

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
        intake_topic_tokens = Counter()
        intake_topic_tokens.update(web_title_tokens.get(month, Counter()))
        intake_topic_tokens.update(chrome_history_title_tokens.get(month, Counter()))
        yt_watch_history_video_ids = youtube_watch_history_video_ids.get(month, Counter())
        yt_watch_history_video_id_top = yt_watch_history_video_ids.most_common(15)
        yt_watch_history_titles = youtube_watch_history_titles.get(month, Counter())
        yt_watch_history_channels = youtube_watch_history_channels.get(month, Counter())
        yt_watch_history_tokens = Counter()

        # Prefer Takeout-provided titles/channels for the full distribution. For older
        # watch-history variants that omit the channel line, fall back to oEmbed.
        if not yt_watch_history_channels:
            for vid, count in yt_watch_history_video_ids.items():
                if not isinstance(vid, str) or not vid:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                    continue
                _, channel = _resolve_youtube_video_meta(vid, takeout_titles=youtube_video_titles, oembed_cache=youtube_oembed_by_id)
                if channel:
                    yt_watch_history_channels[channel] += count

        if not yt_watch_history_titles:
            for vid, count in yt_watch_history_video_ids.items():
                if not isinstance(vid, str) or not vid:
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                    continue
                title, _ = _resolve_youtube_video_meta(vid, takeout_titles=youtube_video_titles, oembed_cache=youtube_oembed_by_id)
                if title:
                    yt_watch_history_titles[title] += count
                else:
                    yt_watch_history_titles[vid] += count

        for title, count in yt_watch_history_titles.items():
            if not isinstance(title, str) or not title:
                continue
            for tok in tokenize_topic(title):
                yt_watch_history_tokens[tok] += count

        intake_topic_tokens.update(yt_watch_history_tokens)
        for phrase, count in google_search_phrases.get(month, Counter()).most_common(200):
            for tok in tokenize_topic(phrase):
                intake_topic_tokens[tok] += count
        for phrase, count in youtube_search_phrases.get(month, Counter()).most_common(200):
            for tok in tokenize_topic(phrase):
                intake_topic_tokens[tok] += count
        for phrase, count in youtube_search_history_phrases.get(month, Counter()).most_common(200):
            for tok in tokenize_topic(phrase):
                intake_topic_tokens[tok] += count
        myactivity_other = myactivity_other_counts.get(month, Counter())
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
            "work": {
                "git_commits": git_commit_counts.get(month, 0),
                "git_top_repos": git_commit_repos.get(month, Counter()).most_common(10),
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
                "youtube_watch_history": youtube_watch_history_counts.get(month, 0),
                "youtube_watch_history_top_video_ids": yt_watch_history_video_id_top,
                "youtube_watch_history_top_channels": yt_watch_history_channels.most_common(15),
                "youtube_watch_history_top_tokens": yt_watch_history_tokens.most_common(15),
                "youtube_watch_history_top_titles": yt_watch_history_titles.most_common(15),
                "youtube_search_history": youtube_search_history_counts.get(month, 0),
                "youtube_search_history_top_tokens": youtube_search_history_tokens.get(month, Counter()).most_common(15),
                "youtube_search_history_top_queries": youtube_search_history_phrases.get(month, Counter()).most_common(15),
                "chrome_myactivity": chrome_counts.get(month, 0),
                "chrome_history_events": chrome_history_counts.get(month, 0),
                "chrome_history_top_domains": chrome_history_domains.get(month, Counter()).most_common(15),
                "chrome_history_top_reddit_subs": chrome_history_reddit_subs.get(month, Counter()).most_common(15),
                "chrome_history_top_title_tokens": chrome_history_title_tokens.get(month, Counter()).most_common(15),
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
                "myactivity_other_categories": myactivity_other.most_common(),
                "spotify_hours": round(spotify_hours.get(month, 0.0), 1) if month in spotify_hours else None,
                "spotify_top_artists": top_artists,
                "spotify_top_tracks": top_tracks,
                "intake_top_topic_tokens": intake_topic_tokens.most_common(20),
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
            "reddit_comments_csv": str(reddit_comments) if reddit_comments else None,
            "reddit_posts_csv": str(reddit_posts) if reddit_posts else None,
            "reddit_messages_csv": str(reddit_messages) if reddit_messages else None,
            "wykop_link_comments_jsonl": str(wykop_link_comments),
            "wykop_entries_jsonl": str(wykop_entries),
            "wykop_entry_comments_jsonl": str(wykop_entry_comments),
            "webhistory_source": webhistory_source,
            "webhistory_ndjson": str(webhistory),
            "webhistory_gestalt_dir": str(webhistory_gestalt_dir) if webhistory_gestalt_dir is not None else None,
            "google_takeouts": [str(p) for p in takeout_paths_used],
            "chrome_history_json": (
                f"{chrome_history_takeout_path}:{'Takeout/Chrome/History.json'}" if chrome_history_takeout_path else None
            ),
            "youtube_watch_history_html": "Takeout/YouTube and YouTube Music/history/watch-history.html",
            "youtube_search_history_html": "Takeout/YouTube and YouTube Music/history/search-history.html",
            "youtube_video_texts_csv": (
                f"{youtube_video_texts_takeout_path}:{'Takeout/YouTube and YouTube Music/video metadata/video texts.csv'}"
                if youtube_video_texts_takeout_path
                else None
            ),
            "youtube_oembed_cache_jsonl": str(youtube_oembed_cache) if youtube_oembed_cache.exists() else None,
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
            "samsung_health_export": str(samsung_health_export),
            "onenote_journal": str(onenote_journal),
            "substance_log": str(substance_log),
            "raindrop_bookmarks": str(raindrop_bookmarks),
            "goodreads_library_csv": str(goodreads_library),
            "spotify_dir": str(resolved_spotify_dir),
            "git_repos": [str(p) for p in git_repos],
        },
        "output_path": str(output),
        "months": monthly,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.secho(f"Wrote {len(months)} months → {output}", fg=typer.colors.GREEN)

    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown(payload), encoding="utf-8")
        typer.secho(f"Wrote Markdown summary → {markdown_output}", fg=typer.colors.GREEN)
    if markdown_output_dir is not None:
        markdown_output_dir.mkdir(parents=True, exist_ok=True)
        index_lines: List[str] = []
        index_lines.append(f"# Life timeline drilldowns ({start_month} → {end_month})")
        index_lines.append("")
        index_lines.append(f"Generated: `{payload.get('generated_at')}`")
        index_lines.append(f"Backing JSON: `{output}`")
        index_lines.append("")
        years = sorted({m.split('-', 1)[0] for m in months})
        index_lines.append("## Years")
        index_lines.append("")
        for year in years:
            index_lines.append(f"- `{year}.md`")
        index_lines.append("")
        (markdown_output_dir / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

        for year in years:
            year_months = {m: payload["months"][m] for m in payload["months"].keys() if m.startswith(f"{year}-")}
            if not year_months:
                continue
            year_start = min(year_months.keys())
            year_end = max(year_months.keys())
            year_payload = {
                "generated_at": payload.get("generated_at"),
                "range": {"start_month": year_start, "end_month": year_end},
                "output_path": str(output),
                "months": year_months,
            }
            (markdown_output_dir / f"{year}.md").write_text(render_markdown(year_payload), encoding="utf-8")
        typer.secho(f"Wrote Markdown drilldowns → {markdown_output_dir}", fg=typer.colors.GREEN)

    artifact_paths: Dict[str, Path] = {"output": output}
    if markdown_output is not None:
        artifact_paths["markdown_output"] = markdown_output
    if markdown_output_dir is not None:
        artifact_paths["markdown_output_dir"] = markdown_output_dir
    artifact_paths["youtube_oembed_cache"] = youtube_oembed_cache

    payload_months = payload.get("months", {})
    if not isinstance(payload_months, dict):
        payload_months = {}

    return LifeTimelineResult(
        output=output,
        start_month=start_month,
        end_month=end_month,
        month_count=len(payload_months),
        artifact_paths=artifact_paths,
    )


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
    output_path = payload.get("output_path")

    lines: List[str] = []
    lines.append(f"# Life timeline auto-summary ({start_month} → {end_month})")
    lines.append("")
    lines.append(f"Generated: `{generated_at}`")
    if output_path:
        lines.append(f"Backing JSON: `{output_path}`")
    lines.append("")
    for month in sorted(months.keys()):
        m = months[month]
        out = m.get("output") or {}
        work = m.get("work") or {}
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
        lines.append(f"- Work: git commits {work.get('git_commits', 0)}.")
        lines.append(
            "- Intake: "
            f"Google searches {intake.get('google_searches', 0)}; "
            f"YouTube watch {intake.get('youtube_watch', 0)}, YouTube searches {intake.get('youtube_searches', 0)}; "
            f"YouTube watch-history {intake.get('youtube_watch_history', 0)}, YouTube search-history {intake.get('youtube_search_history', 0)}; "
            f"Webhistory events {intake.get('webhistory_events', 0)}; "
            f"Chrome MyActivity {intake.get('chrome_myactivity', 0)}; "
            f"Chrome History {intake.get('chrome_history_events', 0)}; "
            f"Maps MyActivity {intake.get('maps_myactivity', 0)}, Image Search MyActivity {intake.get('image_search_myactivity', 0)}, "
            f"Play Store MyActivity {intake.get('play_store_myactivity', 0)}; "
            f"Video Search MyActivity {intake.get('video_search_myactivity', 0)}, Shopping MyActivity {intake.get('shopping_myactivity', 0)}, "
            f"Travel MyActivity {intake.get('travel_myactivity', 0)}; "
            f"Raindrop bookmarks {intake.get('raindrop_bookmarks', 0)}; "
            f"Goodreads read {intake.get('goodreads_books_read', 0)}, added {intake.get('goodreads_books_added', 0)}."
        )
        other_myactivity = intake.get("myactivity_other_categories") or []
        if other_myactivity:
            lines.append(f"- Intake: other MyActivity categories (top): {_render_counter(other_myactivity)}")
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

        lines.append("**Work (top)**")
        lines.append("")
        lines.append(f"- Git top repos: {_render_counter(work.get('git_top_repos') or [])}")
        lines.append("")

        lines.append("**Intake (top)**")
        lines.append("")
        lines.append(f"- Webhistory top domains: {_render_counter(intake.get('webhistory_top_domains') or [])}")
        lines.append(f"- Webhistory top Reddit subs visited: {_render_counter(intake.get('webhistory_top_reddit_subs') or [])}")
        lines.append(f"- Webhistory title top tokens: {_render_counter(intake.get('webhistory_top_title_tokens') or [])}")
        lines.append(f"- Chrome History top domains: {_render_counter(intake.get('chrome_history_top_domains') or [])}")
        lines.append(f"- Chrome History top Reddit subs visited: {_render_counter(intake.get('chrome_history_top_reddit_subs') or [])}")
        lines.append(f"- Chrome History title top tokens: {_render_counter(intake.get('chrome_history_top_title_tokens') or [])}")
        lines.append(f"- Google search top tokens: {_render_counter(intake.get('google_search_top_tokens') or [])}")
        lines.append(f"- Google search top exact queries: {_render_counter(intake.get('google_search_top_queries') or [])}")
        lines.append(f"- YouTube search top tokens: {_render_counter(intake.get('youtube_search_top_tokens') or [])}")
        lines.append(f"- YouTube search top exact queries: {_render_counter(intake.get('youtube_search_top_queries') or [])}")
        lines.append(f"- YouTube watch-history top video IDs: {_render_counter(intake.get('youtube_watch_history_top_video_ids') or [])}")
        lines.append(f"- YouTube watch-history top channels: {_render_counter(intake.get('youtube_watch_history_top_channels') or [])}")
        lines.append(f"- YouTube watch-history top tokens: {_render_counter(intake.get('youtube_watch_history_top_tokens') or [])}")
        lines.append(f"- YouTube watch-history top titles: {_render_counter(intake.get('youtube_watch_history_top_titles') or [])}")
        lines.append(f"- YouTube search-history top tokens: {_render_counter(intake.get('youtube_search_history_top_tokens') or [])}")
        lines.append(f"- YouTube search-history top queries: {_render_counter(intake.get('youtube_search_history_top_queries') or [])}")
        lines.append(f"- Maps search top queries: {_render_counter(intake.get('maps_search_top_queries') or [])}")
        lines.append(f"- Video search top queries: {_render_counter(intake.get('video_search_top_queries') or [])}")
        lines.append(f"- MyActivity other categories: {_render_counter(intake.get('myactivity_other_categories') or [])}")
        lines.append(f"- Goodreads top authors read: {_render_counter(intake.get('goodreads_top_authors_read') or [])}")
        lines.append(f"- Goodreads top titles read: {_render_counter(intake.get('goodreads_top_titles_read') or [])}")
        lines.append(f"- Intake topic tokens: {_render_counter(intake.get('intake_top_topic_tokens') or [])}")
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
