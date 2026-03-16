#!/usr/bin/env python3
"""Build monthly "life timeline" metrics from local personal telemetry sources.

This script is intentionally high-sensitivity: it touches finance/health exports, web
history, Takeout, and private comms metadata. It is meant to run locally only.

Primary output:
- artefacts/lifelog/life-timeline/monthly_life_latest.json (default range)
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import typer

from lynchpin.core.config import get_config
from lynchpin.context import life_timeline as life_context
from lynchpin.sources.captures import webhistory as lp_webhistory
from lynchpin.sources.exports import goodreads as lp_goodreads
from lynchpin.sources.exports import health as lp_health
from lynchpin.sources.exports import raindrop as lp_raindrop
from lynchpin.sources.exports import reddit as lp_reddit
from lynchpin.sources.exports import spotify as lp_spotify
from lynchpin.sources.exports import takeout as lp_takeout
from lynchpin.sources.exports import wykop as lp_wykop
from lynchpin.sources.indices import gitstats as lp_gitstats
from lynchpin.sources.libraries import finance as lp_finance
from lynchpin.sources.libraries import knowledgebase as lp_knowledgebase
from lynchpin.system.life_timeline_paths import (
    DEFAULT_LIFE_TIMELINE_START,
    LATEST_LIFE_TIMELINE_JSON,
    LATEST_LIFE_TIMELINE_DRILLDOWN_DIR,
    YOUTUBE_OEMBED_CACHE,
    current_month_key,
)

app = typer.Typer(pretty_exceptions_show_locals=False)


@dataclass(frozen=True)
class LifeTimelineResult:
    output: Path
    start_month: str
    end_month: str
    month_count: int
    artifact_paths: Dict[str, Path]


@contextmanager
def _stage(label: str) -> Iterator[None]:
    start = time.monotonic()
    typer.echo(f"[life-timeline] {label}…", err=True)
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        typer.echo(f"[life-timeline] {label} done in {elapsed:.1f}s", err=True)


def iter_months(start_month: str, end_month: str) -> Iterator[str]:
    year, month = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_i = (int(part) for part in end_month.split("-", 1))
    while (year, month) <= (end_year, end_month_i):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month == 13:
            month = 1
            year += 1


@app.command()
def narrative(
    keys: List[str] = typer.Argument(..., help="Keys to generate narratives for (scale-dependent format)."),
    scale: str = typer.Option("month", help="Scale: day | week | episode | quarter | month"),
    batch: bool = typer.Option(False, "--batch/--no-batch", help="Generate all keys concurrently (max 3)."),
) -> None:
    """Generate prose retrospective narratives at day/week/episode/quarter/month scale."""
    import asyncio
    from lynchpin.context.narrative import (
        NarrativeKind,
        build_day_prompt,
        build_week_prompt,
        build_episode_prompt,
        build_quarter_prompt,
        build_month_prompt,
        generate_narrative,
        generate_batch,
    )

    _VALID_SCALES = {"day", "week", "episode", "quarter", "month"}
    if scale not in _VALID_SCALES:
        typer.echo(f"[narrative] Unknown scale '{scale}'. Choose from: {', '.join(sorted(_VALID_SCALES))}", err=True)
        raise typer.Exit(1)

    kind = NarrativeKind(scale)

    if scale == "month":
        from lynchpin.context.life_timeline import build_recent_trajectory_summaries

        trajectory_months, _ = build_recent_trajectory_summaries(
            keys,
            lookback_days=365 * 10,
        )
        prompts = []
        for key in sorted(keys):
            traj = trajectory_months.get(key)
            if traj is None:
                typer.echo(f"[narrative] No trajectory data for {key}, skipping.", err=True)
                continue
            prompts.append((build_month_prompt(traj, month_key=key), kind, key))

    elif scale == "quarter":
        from lynchpin.trajectory import summarize_quarters, summarize_trajectory_months
        from lynchpin.trajectory.day import summarize_days

        days = summarize_days()
        months = summarize_trajectory_months(days)
        quarters = summarize_quarters(months)
        q_by_key = {q.quarter: q for q in quarters}
        prompts = []
        for key in sorted(keys):
            q = q_by_key.get(key)
            if q is None:
                typer.echo(f"[narrative] No trajectory data for {key}, skipping.", err=True)
                continue
            prompts.append((build_quarter_prompt(q), kind, key))

    elif scale == "week":
        from lynchpin.trajectory import summarize_weeks
        from lynchpin.trajectory.day import summarize_days

        days = summarize_days()
        weeks = summarize_weeks(days)
        w_by_key = {w.week: w for w in weeks}
        d_by_w: dict[str, list] = {}
        for d in days:
            iso = d.date.isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
            d_by_w.setdefault(wk, []).append(d)
        prompts = []
        for key in sorted(keys):
            w = w_by_key.get(key)
            if w is None:
                typer.echo(f"[narrative] No trajectory data for {key}, skipping.", err=True)
                continue
            prompts.append((build_week_prompt(w, days=d_by_w.get(key, [])), kind, key))

    elif scale == "day":
        from lynchpin.trajectory.day import summarize_days

        days = summarize_days()
        d_by_key = {str(d.date): d for d in days}
        prompts = []
        for key in sorted(keys):
            d = d_by_key.get(key)
            if d is None:
                typer.echo(f"[narrative] No trajectory data for {key}, skipping.", err=True)
                continue
            prompts.append((build_day_prompt(d), kind, key))

    elif scale == "episode":
        from lynchpin.trajectory.day import summarize_days
        from lynchpin.trajectory.episode import detect_episodes

        days = summarize_days()
        episodes = detect_episodes(days)
        ep_by_key = {ep.episode_id: ep for ep in episodes}
        d_by_ep: dict[str, list] = {}
        for ep in episodes:
            d_by_ep[ep.episode_id] = [d for d in days if ep.start_date <= d.date <= ep.end_date]
        prompts = []
        for key in sorted(keys):
            ep = ep_by_key.get(key)
            if ep is None:
                typer.echo(f"[narrative] No episode '{key}'.", err=True)
                continue
            prompts.append((build_episode_prompt(ep, days=d_by_ep.get(key, [])), kind, key))

    else:
        prompts = []

    if not prompts:
        typer.echo("[narrative] Nothing to generate.", err=True)
        return

    if batch and len(prompts) > 1:
        typer.echo(f"[narrative] Batch generating {len(prompts)} {scale} narratives…", err=True)
        results = asyncio.run(generate_batch(prompts))
        for result in results:
            typer.secho(f"\n## {result.key}\n", fg=typer.colors.CYAN)
            typer.echo(result.text)
            typer.echo(f"\n[tokens: in={result.input_tokens} out={result.output_tokens} cost=${result.cost_usd:.4f}]", err=True)
    else:
        for prompt_text, k, key in prompts:
            typer.echo(f"[narrative] Generating {scale} for {key}…", err=True)
            result = asyncio.run(generate_narrative(prompt_text, k, key))
            typer.secho(f"\n## {key}\n", fg=typer.colors.CYAN)
            typer.echo(result.text)
            typer.echo(f"\n[tokens: in={result.input_tokens} out={result.output_tokens} cost=${result.cost_usd:.4f}]", err=True)


@app.command()
def build(
    start: str = typer.Option(DEFAULT_LIFE_TIMELINE_START, help="Start month (YYYY-MM)."),
    end: str = typer.Option(current_month_key(), help="End month (YYYY-MM). Defaults to the current month."),
    output: Path = typer.Option(
        LATEST_LIFE_TIMELINE_JSON,
        help="Output JSON path (defaults to the canonical latest snapshot).",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        help="Optional Markdown summary output (human-readable drilldown).",
    ),
    markdown_output_dir: Optional[Path] = typer.Option(
        None,
        help=f"Optional directory for per-year Markdown drilldowns (canonical latest path: {LATEST_LIFE_TIMELINE_DRILLDOWN_DIR}).",
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
    takeout: List[Path] = typer.Option(
        [],
        "--takeout",
        help="Optional explicit Google Takeout seed archive(s); defaults to takeout*-001.tgz under --takeout-root.",
    ),
    generate_narratives: bool = typer.Option(
        False,
        "--narrative/--no-narrative",
        help="Generate LLM prose retrospectives for months with trajectory data (requires Claude Max).",
    ),
) -> LifeTimelineResult:
    start_month = start
    end_month = end
    if markdown_output is not None and markdown_output_dir is not None:
        raise ValueError("Pass at most one of --markdown-output or --markdown-output-dir.")

    cfg = get_config()
    months = list(iter_months(start_month, end_month))

    with _stage("Parse Reddit"):
        reddit_summary = lp_reddit.summarize_activity(
            start_month=start_month,
            end_month=end_month,
            comments_paths=[reddit_comments] if reddit_comments else None,
            posts_paths=[reddit_posts] if reddit_posts else None,
            message_paths=[reddit_messages] if reddit_messages else None,
            tokenize_text=lp_takeout.tokenize_topic,
        )
        reddit_comment_counts = reddit_summary.comment_counts
        reddit_comment_subs = reddit_summary.comment_subreddits
        reddit_comment_tokens = reddit_summary.comment_tokens
        reddit_post_counts = reddit_summary.post_counts
        reddit_message_counts = reddit_summary.message_counts

    with _stage("Parse Wykop"):
        wykop_summary = lp_wykop.summarize_activity(
            start_month=start_month,
            end_month=end_month,
            link_comments_path=wykop_link_comments,
            entries_path=wykop_entries,
            entry_comments_path=wykop_entry_comments,
            tokenize_text=lp_takeout.tokenize_topic,
        )
        wykop_link_counts = wykop_summary.link_comment_counts
        wykop_link_tags = wykop_summary.link_comment_tags
        wykop_link_tokens = wykop_summary.link_comment_tokens
        wykop_entry_counts = wykop_summary.entry_counts
        wykop_entry_tags = wykop_summary.entry_tags
        wykop_entry_tokens = wykop_summary.entry_tokens
        wykop_entry_comment_counts = wykop_summary.entry_comment_counts
        wykop_entry_comment_tokens = wykop_summary.entry_comment_tokens

    webhistory_source: str
    with _stage("Parse webhistory"):
        if webhistory_gestalt_dir is not None and webhistory_gestalt_dir.exists():
            webhistory_source = "gestalt"
            web_counts, web_domains, web_reddit_subs, web_title_tokens = lp_webhistory.summarize_gestalt_dir(
                webhistory_gestalt_dir, start_month, end_month
            )
        else:
            webhistory_source = "ndjson"
            web_counts, web_domains, web_reddit_subs, web_title_tokens = lp_webhistory.summarize_ndjson(
                webhistory, start_month, end_month
            )

    resolved_spotify_dir = spotify_dir or cfg.spotify_root
    with _stage("Parse bookmarks/media"):
        raindrop_counts = lp_raindrop.summarize_bookmarks(
            start_month=start_month,
            end_month=end_month,
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
        spotify_summary = lp_spotify.summarize_streaming(start_month, end_month, root=resolved_spotify_dir)
        spotify_hours = spotify_summary.hours
        spotify_artists = spotify_summary.artists
        spotify_tracks = spotify_summary.tracks

    with _stage("Parse finance"):
        ledger_expenses = lp_finance.parse_ledger_expenses(ledger, start_month, end_month)
        revolut_out_annotated, revolut_in_annotated = lp_finance.parse_revolut_statement(
            revolut_annotated, start_month, end_month
        )
        revolut_out_recent, revolut_in_recent = lp_finance.parse_revolut_statement(revolut_recent, start_month, end_month)

        mbank_personal_out, mbank_personal_in = lp_finance.parse_mbank_operations(
            mbank_personal, start_month, end_month
        )
        mbank_business_out, mbank_business_in = lp_finance.parse_mbank_operations(
            mbank_business, start_month, end_month
        )

    with _stage("Parse health"):
        sleep_sessions, sleep_total_hours = lp_health.parse_samsung_health_sleep(
            samsung_health_export,
            start_month,
            end_month,
        )
        weight_values = lp_health.parse_samsung_health_weight(
            samsung_health_export,
            start_month,
            end_month,
        )

    with _stage("Parse notes"):
        onenote_counts = lp_knowledgebase.summarize_onenote_journal_entries(
            onenote_journal, start_month, end_month
        )
        substance_headings = lp_knowledgebase.summarize_substance_log_headings(
            substance_log, start_month, end_month
        )

    with _stage("Parse git activity"):
        git_repos = lp_gitstats.active_repo_paths()
        git_commit_counts, git_commit_repos = lp_gitstats.summarize_commit_activity(
            start_month=start_month,
            end_month=end_month,
            repos=git_repos,
        )

    with _stage("Discover Google Takeout archives"):
        resolved_takeout_root = takeout_root or (cfg.exports_root / "google" / "raw" / "takeout")
        takeout_paths_used = lp_takeout.resolve_archives(explicit_seeds=takeout, root=resolved_takeout_root)
        if not takeout_paths_used:
            raise FileNotFoundError(
                f"No Google Takeout archives found (expected takeout*.tgz under {resolved_takeout_root})."
            )

    with _stage(f"Parse Google Takeout ({len(takeout_paths_used)} archives)"):
        takeout_bundle = lp_takeout.parse_life_timeline_takeouts(
            takeout_paths_used,
            start_month=start_month,
            end_month=end_month,
        )

    google_search_counts = takeout_bundle.google_search_counts
    google_search_tokens = takeout_bundle.google_search_tokens
    google_search_phrases = takeout_bundle.google_search_phrases
    youtube_watch_counts = takeout_bundle.youtube_watch_counts
    youtube_search_counts = takeout_bundle.youtube_search_counts
    youtube_search_tokens = takeout_bundle.youtube_search_tokens
    youtube_search_phrases = takeout_bundle.youtube_search_phrases
    youtube_video_titles = takeout_bundle.youtube_video_titles
    youtube_watch_history_counts = takeout_bundle.youtube_watch_history_counts
    youtube_watch_history_video_ids = takeout_bundle.youtube_watch_history_video_ids
    youtube_watch_history_titles = takeout_bundle.youtube_watch_history_titles
    youtube_watch_history_channels = takeout_bundle.youtube_watch_history_channels
    youtube_search_history_counts = takeout_bundle.youtube_search_history_counts
    youtube_search_history_tokens = takeout_bundle.youtube_search_history_tokens
    youtube_search_history_phrases = takeout_bundle.youtube_search_history_phrases
    chrome_counts = takeout_bundle.chrome_counts
    maps_counts = takeout_bundle.maps_counts
    maps_tokens = takeout_bundle.maps_tokens
    maps_phrases = takeout_bundle.maps_phrases
    image_search_counts = takeout_bundle.image_search_counts
    image_search_tokens = takeout_bundle.image_search_tokens
    image_search_phrases = takeout_bundle.image_search_phrases
    play_store_counts = takeout_bundle.play_store_counts
    play_store_tokens = takeout_bundle.play_store_tokens
    play_store_phrases = takeout_bundle.play_store_phrases
    video_search_counts = takeout_bundle.video_search_counts
    video_search_tokens = takeout_bundle.video_search_tokens
    video_search_phrases = takeout_bundle.video_search_phrases
    shopping_counts = takeout_bundle.shopping_counts
    shopping_tokens = takeout_bundle.shopping_tokens
    shopping_phrases = takeout_bundle.shopping_phrases
    travel_counts = takeout_bundle.travel_counts
    travel_tokens = takeout_bundle.travel_tokens
    travel_phrases = takeout_bundle.travel_phrases
    myactivity_other_counts = takeout_bundle.myactivity_other_counts
    chrome_history_counts = takeout_bundle.chrome_history_counts
    chrome_history_domains = takeout_bundle.chrome_history_domains
    chrome_history_reddit_subs = takeout_bundle.chrome_history_reddit_subs
    chrome_history_title_tokens = takeout_bundle.chrome_history_title_tokens
    location_records = takeout_bundle.location_records
    semantic_place_visits = takeout_bundle.semantic_place_visits
    semantic_activity_segments = takeout_bundle.semantic_activity_segments
    semantic_top_places = takeout_bundle.semantic_top_places
    semantic_top_activities = takeout_bundle.semantic_top_activities
    gmail_counts = takeout_bundle.gmail_counts
    gmail_from_domains = takeout_bundle.gmail_from_domains
    gmail_subject_tokens = takeout_bundle.gmail_subject_tokens
    location_takeout_path = takeout_bundle.location_takeout_path
    gmail_takeout_path = takeout_bundle.gmail_takeout_path
    chrome_history_takeout_path = takeout_bundle.chrome_history_takeout_path
    youtube_video_texts_takeout_path = takeout_bundle.youtube_video_texts_takeout_path

    youtube_oembed_by_id = lp_takeout.load_youtube_oembed_cache(youtube_oembed_cache)
    trajectory_months, trajectory_window = life_context.build_recent_trajectory_summaries(months)

    monthly: Dict[str, dict] = {}
    for month in months:
        weights = weight_values.get(month, [])
        top_artists = lp_spotify.top_names(spotify_artists, month, limit=3)
        top_tracks = lp_spotify.top_names(spotify_tracks, month, limit=3)
        topic_tokens = Counter()
        topic_tokens.update(reddit_comment_tokens.get(month, Counter()))
        topic_tokens.update(wykop_link_tokens.get(month, Counter()))
        topic_tokens.update(wykop_entry_tokens.get(month, Counter()))
        topic_tokens.update(wykop_entry_comment_tokens.get(month, Counter()))
        intake_topic_tokens = Counter()
        intake_topic_tokens.update(web_title_tokens.get(month, Counter()))
        intake_topic_tokens.update(chrome_history_title_tokens.get(month, Counter()))
        (
            yt_watch_history_video_id_top,
            yt_watch_history_titles,
            yt_watch_history_channels,
            yt_watch_history_tokens,
        ) = lp_takeout.summarize_youtube_watch_history_month(
            youtube_watch_history_video_ids.get(month, Counter()),
            youtube_watch_history_titles.get(month, Counter()),
            youtube_watch_history_channels.get(month, Counter()),
            takeout_titles=youtube_video_titles,
            oembed_cache=youtube_oembed_by_id,
            tokenize_text=lp_takeout.tokenize_topic,
        )

        intake_topic_tokens.update(yt_watch_history_tokens)
        intake_topic_tokens.update(
            lp_takeout.phrase_topic_tokens(
                google_search_phrases.get(month, Counter()),
                tokenize_text=lp_takeout.tokenize_topic,
            )
        )
        intake_topic_tokens.update(
            lp_takeout.phrase_topic_tokens(
                youtube_search_phrases.get(month, Counter()),
                tokenize_text=lp_takeout.tokenize_topic,
            )
        )
        intake_topic_tokens.update(
            lp_takeout.phrase_topic_tokens(
                youtube_search_history_phrases.get(month, Counter()),
                tokenize_text=lp_takeout.tokenize_topic,
            )
        )
        myactivity_other = myactivity_other_counts.get(month, Counter())
        monthly[month] = life_context.build_month_summary(
            output=life_context.build_output_summary(
                month,
                reddit_comment_counts=reddit_comment_counts,
                reddit_post_counts=reddit_post_counts,
                reddit_message_counts=reddit_message_counts,
                wykop_link_counts=wykop_link_counts,
                wykop_entry_counts=wykop_entry_counts,
                wykop_entry_comment_counts=wykop_entry_comment_counts,
                reddit_comment_subs=reddit_comment_subs,
                wykop_link_tags=wykop_link_tags,
                wykop_entry_tags=wykop_entry_tags,
                topic_tokens=topic_tokens,
            ),
            work=life_context.build_work_summary(
                month,
                git_commit_counts=git_commit_counts,
                git_commit_repos=git_commit_repos,
                chat_session_count=trajectory_months[month].chat_session_count if month in trajectory_months else 0,
                chat_work_events=dict(trajectory_months[month].chat_work_events) if month in trajectory_months else {},
            ),
            intake=life_context.build_intake_summary(
                month,
                web_counts=web_counts,
                web_domains=web_domains,
                web_reddit_subs=web_reddit_subs,
                web_title_tokens=web_title_tokens,
                raindrop_counts=raindrop_counts,
                goodreads_read_counts=goodreads_read_counts,
                goodreads_added_counts=goodreads_added_counts,
                goodreads_authors_read=goodreads_authors_read,
                goodreads_titles_read=goodreads_titles_read,
                google_search_counts=google_search_counts,
                google_search_tokens=google_search_tokens,
                google_search_phrases=google_search_phrases,
                youtube_watch_counts=youtube_watch_counts,
                youtube_search_counts=youtube_search_counts,
                youtube_search_tokens=youtube_search_tokens,
                youtube_search_phrases=youtube_search_phrases,
                youtube_watch_history_counts=youtube_watch_history_counts,
                yt_watch_history_video_id_top=yt_watch_history_video_id_top,
                yt_watch_history_channels=yt_watch_history_channels,
                yt_watch_history_tokens=yt_watch_history_tokens,
                yt_watch_history_titles=yt_watch_history_titles,
                youtube_search_history_counts=youtube_search_history_counts,
                youtube_search_history_tokens=youtube_search_history_tokens,
                youtube_search_history_phrases=youtube_search_history_phrases,
                chrome_counts=chrome_counts,
                chrome_history_counts=chrome_history_counts,
                chrome_history_domains=chrome_history_domains,
                chrome_history_reddit_subs=chrome_history_reddit_subs,
                chrome_history_title_tokens=chrome_history_title_tokens,
                maps_counts=maps_counts,
                maps_tokens=maps_tokens,
                maps_phrases=maps_phrases,
                image_search_counts=image_search_counts,
                image_search_tokens=image_search_tokens,
                image_search_phrases=image_search_phrases,
                play_store_counts=play_store_counts,
                play_store_tokens=play_store_tokens,
                play_store_phrases=play_store_phrases,
                video_search_counts=video_search_counts,
                video_search_tokens=video_search_tokens,
                video_search_phrases=video_search_phrases,
                shopping_counts=shopping_counts,
                shopping_tokens=shopping_tokens,
                shopping_phrases=shopping_phrases,
                travel_counts=travel_counts,
                travel_tokens=travel_tokens,
                travel_phrases=travel_phrases,
                myactivity_other=myactivity_other,
                spotify_hours=spotify_hours,
                spotify_top_artists=top_artists,
                spotify_top_tracks=top_tracks,
                intake_topic_tokens=intake_topic_tokens,
            ),
            mail=life_context.build_mail_summary(
                month,
                gmail_counts=gmail_counts,
                gmail_from_domains=gmail_from_domains,
                gmail_subject_tokens=gmail_subject_tokens,
            ),
            location=life_context.build_location_summary(
                month,
                location_records=location_records,
                semantic_place_visits=semantic_place_visits,
                semantic_activity_segments=semantic_activity_segments,
                semantic_top_places=semantic_top_places,
                semantic_top_activities=semantic_top_activities,
            ),
            money=life_context.build_money_summary(
                month,
                ledger_expenses=ledger_expenses,
                revolut_out_annotated=revolut_out_annotated,
                revolut_out_recent=revolut_out_recent,
                revolut_in_annotated=revolut_in_annotated,
                revolut_in_recent=revolut_in_recent,
                mbank_personal_out=mbank_personal_out,
                mbank_personal_in=mbank_personal_in,
                mbank_business_out=mbank_business_out,
                mbank_business_in=mbank_business_in,
            ),
            health=life_context.build_health_summary(
                month,
                sleep_sessions=sleep_sessions,
                sleep_total_hours=sleep_total_hours,
                weights=weights,
            ),
            notes=life_context.build_notes_summary(
                month,
                onenote_counts=onenote_counts,
                substance_headings=substance_headings,
            ),
            trajectory=trajectory_months.get(month),
        ).to_dict()

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
            "finance_revolut_annotated": str(revolut_annotated),
            "finance_revolut_recent": str(revolut_recent),
            "finance_mbank_personal": str(mbank_personal),
            "finance_mbank_business": str(mbank_business),
            "samsung_health_export": str(samsung_health_export),
            "onenote_journal": str(onenote_journal),
            "substance_log": str(substance_log),
            "raindrop_bookmarks": str(raindrop_bookmarks),
            "goodreads_library_csv": str(goodreads_library),
            "spotify_dir": str(resolved_spotify_dir),
            "git_repos": [str(p) for p in git_repos],
            "recent_trajectory_window": trajectory_window if trajectory_window.get("month_count") else None,
        },
        "output_path": str(output),
        "months": monthly,
    }

    if generate_narratives:
        import asyncio
        from lynchpin.context.narrative import NarrativeKind, build_month_prompt, generate_narrative

        with _stage("Generate LLM narratives"):
            for month in months:
                traj = trajectory_months.get(month)
                if traj is None:
                    continue
                prompt = build_month_prompt(traj, month_key=month)
                try:
                    result = asyncio.run(generate_narrative(prompt, NarrativeKind.month, month))
                    if result.text and month in monthly:
                        monthly[month]["narrative"] = result.text
                    payload["months"] = monthly
                except Exception as exc:
                    typer.echo(f"[narrative] {month} failed: {exc}", err=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.secho(f"Wrote {len(months)} months → {output}", fg=typer.colors.GREEN)

    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(life_context.render_markdown(payload), encoding="utf-8")
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
            (markdown_output_dir / f"{year}.md").write_text(life_context.render_markdown(year_payload), encoding="utf-8")
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


if __name__ == "__main__":
    app()
