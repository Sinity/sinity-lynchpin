#!/usr/bin/env python3
"""CLI for long-range life summary artefact builds."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

import structlog as _structlog

if not _structlog.is_configured():
    _structlog.configure(wrapper_class=_structlog.make_filtering_bound_logger(40))

from .life_paths import (
    DEFAULT_LIFE_START,
    LATEST_LIFE_DRILLDOWN_DIR,
    LATEST_LIFE_JSON,
    YOUTUBE_OEMBED_CACHE,
    current_month_key,
)
from .life_range import build_life_range
from .life_range_models import LifeRangeInputs, LifeRangeResult

app = typer.Typer(pretty_exceptions_show_locals=False)


@app.command()
def build(
    start: str = typer.Option(DEFAULT_LIFE_START, help="Start month (YYYY-MM)."),
    end: str = typer.Option(current_month_key(), help="End month (YYYY-MM). Defaults to the current month."),
    output: Path = typer.Option(
        LATEST_LIFE_JSON,
        help="Output JSON path (defaults to the canonical latest life snapshot).",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        help="Optional Markdown summary output (human-readable drilldown).",
    ),
    markdown_output_dir: Optional[Path] = typer.Option(
        None,
        help=f"Optional directory for per-year Markdown drilldowns (canonical latest path: {LATEST_LIFE_DRILLDOWN_DIR}).",
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
        help="Optional gestalt segment directory override; when provided it is parsed instead of the canonical NDJSON.",
    ),
    youtube_oembed_cache: Path = typer.Option(
        YOUTUBE_OEMBED_CACHE,
        help="Optional JSONL cache for YouTube oEmbed lookups (video_id -> title/channel).",
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
    revolut_annotated: Path = typer.Option(
        Path("/realm/data/libraries/finance/data/statements/revolut_ANNOTATED_PLN_statement_2019_09_01_2022_05_01.csv"),
        help="Annotated Revolut statement CSV covering the earlier range.",
    ),
    revolut_recent: Path = typer.Option(
        Path("/realm/data/libraries/finance/data/statements/newest/REVOLUT_PLN_account-statement_2022-10-02_2023-02-22_en-us_cea3dc.csv"),
        help="Recent Revolut statement CSV.",
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
    takeout_root: Optional[Path] = typer.Option(
        None,
        help="Directory containing canonical Google Takeout .tgz archives (used when --takeout is omitted).",
    ),
    takeout: list[Path] = typer.Option(
        [],
        "--takeout",
        help="Optional explicit Google Takeout seed archive(s); defaults to takeout*-001.tgz under --takeout-root.",
    ),
) -> LifeRangeResult:
    inputs = LifeRangeInputs(
        wykop_link_comments=wykop_link_comments,
        wykop_entries=wykop_entries,
        wykop_entry_comments=wykop_entry_comments,
        reddit_comments=reddit_comments,
        reddit_posts=reddit_posts,
        reddit_messages=reddit_messages,
        webhistory=webhistory,
        webhistory_gestalt_dir=webhistory_gestalt_dir,
        youtube_oembed_cache=youtube_oembed_cache,
        raindrop_bookmarks=raindrop_bookmarks,
        goodreads_library=goodreads_library,
        spotify_dir=spotify_dir,
        ledger=ledger,
        revolut_annotated=revolut_annotated,
        revolut_recent=revolut_recent,
        mbank_personal=mbank_personal,
        mbank_business=mbank_business,
        samsung_health_export=samsung_health_export,
        onenote_journal=onenote_journal,
        substance_log=substance_log,
        takeout_root=takeout_root,
        takeout_paths=tuple(takeout),
    )
    return build_life_range(
        start_month=start,
        end_month=end,
        output=output,
        inputs=inputs,
        markdown_output=markdown_output,
        markdown_output_dir=markdown_output_dir,
    )


if __name__ == "__main__":
    app()
