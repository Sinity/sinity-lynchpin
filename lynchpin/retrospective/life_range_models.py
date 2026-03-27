from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from lynchpin.sources.exports.reddit import RedditActivitySummary
from lynchpin.sources.exports.spotify import SpotifyStreamingSummary
from lynchpin.sources.exports.takeout_life import LifeTakeoutBundle
from lynchpin.sources.exports.wykop import WykopActivitySummary

from .life_paths import YOUTUBE_OEMBED_CACHE
from .life_summary_models import LifeMonthContextSummary


@dataclass(frozen=True)
class LifeRangeInputs:
    wykop_link_comments: Path = Path("/realm/data/exports/wykop/raw/Sinity/wykop_links_commented.jsonl")
    wykop_entries: Path = Path("/realm/data/exports/wykop/raw/Sinity/wykop_entries_added.jsonl")
    wykop_entry_comments: Path = Path("/realm/data/exports/wykop/raw/Sinity/wykop_entry_comments.jsonl")
    reddit_comments: Path | None = None
    reddit_posts: Path | None = None
    reddit_messages: Path | None = None
    webhistory: Path = Path("/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson")
    webhistory_gestalt_dir: Path | None = None
    youtube_oembed_cache: Path = YOUTUBE_OEMBED_CACHE
    raindrop_bookmarks: Path = Path("/realm/data/exports/raindrop/raw/raindrop_bookmarks_19_08_2025.csv")
    goodreads_library: Path = Path("/realm/data/exports/goodreads/raw/library_export.csv")
    spotify_dir: Path | None = None
    ledger: Path = Path("/realm/data/libraries/finance/journal_clean")
    revolut_annotated: Path = Path(
        "/realm/data/libraries/finance/data/statements/revolut_ANNOTATED_PLN_statement_2019_09_01_2022_05_01.csv"
    )
    revolut_recent: Path = Path(
        "/realm/data/libraries/finance/data/statements/newest/REVOLUT_PLN_account-statement_2022-10-02_2023-02-22_en-us_cea3dc.csv"
    )
    mbank_personal: Path = Path(
        "/realm/data/libraries/finance/data/statements/newest/mbank_personal_lista_operacji_220222_230222_202302220823535351.csv"
    )
    mbank_business: Path = Path(
        "/realm/data/libraries/finance/data/statements/newest/mbank_business_lista_operacji_220222_230222_202302220825097527.csv"
    )
    samsung_health_export: Path = Path("/realm/data/exports/health/raw/samsung-health")
    onenote_journal: Path = Path("/realm/project/knowledgebase/logs.log-journal-onenote-2020.md")
    substance_log: Path = Path("/realm/project/knowledgebase/logs.log-substance.md")
    takeout_root: Path | None = None
    takeout_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class LifeRangeResult:
    output: Path
    start_month: str
    end_month: str
    month_count: int
    artifact_paths: dict[str, Path]


@dataclass(frozen=True)
class LifeWebHistorySummary:
    source: str
    counts: dict[str, int]
    domains: dict[str, Counter[str]]
    reddit_subs: dict[str, Counter[str]]
    title_tokens: dict[str, Counter[str]]


@dataclass(frozen=True)
class LifeGoodreadsSummary:
    read_counts: dict[str, int]
    added_counts: dict[str, int]
    authors_read: dict[str, Counter[str]]
    titles_read: dict[str, Counter[str]]


@dataclass(frozen=True)
class LifeFinanceSummary:
    ledger_expenses: dict[str, float]
    revolut_out_annotated: dict[str, float]
    revolut_in_annotated: dict[str, float]
    revolut_out_recent: dict[str, float]
    revolut_in_recent: dict[str, float]
    mbank_personal_out: dict[str, float]
    mbank_personal_in: dict[str, float]
    mbank_business_out: dict[str, float]
    mbank_business_in: dict[str, float]


@dataclass(frozen=True)
class LifeHealthSummary:
    sleep_sessions: dict[str, int]
    sleep_total_hours: dict[str, float]
    weight_values: dict[str, list[float]]


@dataclass(frozen=True)
class LifeNotesSummary:
    onenote_counts: dict[str, int]
    substance_headings: dict[str, int]


@dataclass(frozen=True)
class LifeGitSummary:
    repos: tuple[Path, ...]
    commit_counts: dict[str, int]
    commit_repos: dict[str, Counter[str]]


@dataclass(frozen=True)
class LifeRangeEvidence:
    reddit: RedditActivitySummary
    wykop: WykopActivitySummary
    webhistory: LifeWebHistorySummary
    raindrop_counts: dict[str, int]
    goodreads: LifeGoodreadsSummary
    spotify: SpotifyStreamingSummary
    finance: LifeFinanceSummary
    health: LifeHealthSummary
    notes: LifeNotesSummary
    git: LifeGitSummary
    takeout_paths: tuple[Path, ...]
    takeout: LifeTakeoutBundle
    youtube_oembed_by_id: dict[str, dict[str, object]]
    context_months: dict[str, LifeMonthContextSummary]
    context_window: dict[str, object]
    resolved_spotify_dir: Path | None
