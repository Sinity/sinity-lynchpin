from __future__ import annotations

import json
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from lynchpin.core.config import get_config
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
from .life_paths import (
    YOUTUBE_OEMBED_CACHE,
    current_month_key,
)
from .life_summary import (
    build_health_summary,
    build_intake_summary,
    build_location_summary,
    build_mail_summary,
    build_month_summary,
    build_money_summary,
    build_notes_summary,
    build_output_summary,
    build_recent_context_summaries,
    build_work_summary,
    render_markdown,
)


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


def iter_months(start_month: str, end_month: str) -> Iterator[str]:
    year, month = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_i = (int(part) for part in end_month.split("-", 1))
    while (year, month) <= (end_year, end_month_i):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month == 13:
            month = 1
            year += 1


@contextmanager
def _stage(label: str) -> Iterator[None]:
    start = time.monotonic()
    print(f"[life-range] {label}…", file=sys.stderr, flush=True)
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        print(f"[life-range] {label} done in {elapsed:.1f}s", file=sys.stderr, flush=True)


def build_life_range(
    *,
    start_month: str,
    end_month: str | None = None,
    output: Path,
    inputs: LifeRangeInputs | None = None,
    markdown_output: Optional[Path] = None,
    markdown_output_dir: Optional[Path] = None,
) -> LifeRangeResult:
    resolved_end_month = end_month or current_month_key()
    resolved_inputs = inputs or LifeRangeInputs()
    if markdown_output is not None and markdown_output_dir is not None:
        raise ValueError("Pass at most one of markdown_output or markdown_output_dir.")

    cfg = get_config()
    months = list(iter_months(start_month, resolved_end_month))

    with _stage("Parse Reddit"):
        reddit_summary = lp_reddit.summarize_activity(
            start_month=start_month,
            end_month=resolved_end_month,
            comments_paths=[resolved_inputs.reddit_comments] if resolved_inputs.reddit_comments else None,
            posts_paths=[resolved_inputs.reddit_posts] if resolved_inputs.reddit_posts else None,
            message_paths=[resolved_inputs.reddit_messages] if resolved_inputs.reddit_messages else None,
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
            end_month=resolved_end_month,
            link_comments_path=resolved_inputs.wykop_link_comments,
            entries_path=resolved_inputs.wykop_entries,
            entry_comments_path=resolved_inputs.wykop_entry_comments,
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

    with _stage("Parse webhistory"):
        if resolved_inputs.webhistory_gestalt_dir is not None and resolved_inputs.webhistory_gestalt_dir.exists():
            webhistory_source = "gestalt"
            web_counts, web_domains, web_reddit_subs, web_title_tokens = lp_webhistory.summarize_gestalt_dir(
                resolved_inputs.webhistory_gestalt_dir, start_month, resolved_end_month
            )
        else:
            webhistory_source = "ndjson"
            web_counts, web_domains, web_reddit_subs, web_title_tokens = lp_webhistory.summarize_ndjson(
                resolved_inputs.webhistory, start_month, resolved_end_month
            )

    resolved_spotify_dir = resolved_inputs.spotify_dir or cfg.spotify_root
    with _stage("Parse bookmarks/media"):
        raindrop_counts = lp_raindrop.summarize_bookmarks(
            start_month=start_month,
            end_month=resolved_end_month,
            csv_path=resolved_inputs.raindrop_bookmarks,
        )
        (
            goodreads_read_counts,
            goodreads_added_counts,
            goodreads_authors_read,
            goodreads_titles_read,
        ) = lp_goodreads.summarize_library(start_month, resolved_end_month, path=resolved_inputs.goodreads_library)
        spotify_summary = lp_spotify.summarize_streaming(start_month, resolved_end_month, root=resolved_spotify_dir)
        spotify_hours = spotify_summary.hours
        spotify_artists = spotify_summary.artists
        spotify_tracks = spotify_summary.tracks

    with _stage("Parse finance"):
        ledger_expenses = lp_finance.parse_ledger_expenses(resolved_inputs.ledger, start_month, resolved_end_month)
        revolut_out_annotated, revolut_in_annotated = lp_finance.parse_revolut_statement(
            resolved_inputs.revolut_annotated, start_month, resolved_end_month
        )
        revolut_out_recent, revolut_in_recent = lp_finance.parse_revolut_statement(
            resolved_inputs.revolut_recent, start_month, resolved_end_month
        )
        mbank_personal_out, mbank_personal_in = lp_finance.parse_mbank_operations(
            resolved_inputs.mbank_personal, start_month, resolved_end_month
        )
        mbank_business_out, mbank_business_in = lp_finance.parse_mbank_operations(
            resolved_inputs.mbank_business, start_month, resolved_end_month
        )

    with _stage("Parse health"):
        sleep_sessions, sleep_total_hours = lp_health.parse_samsung_health_sleep(
            resolved_inputs.samsung_health_export,
            start_month,
            resolved_end_month,
        )
        weight_values = lp_health.parse_samsung_health_weight(
            resolved_inputs.samsung_health_export,
            start_month,
            resolved_end_month,
        )

    with _stage("Parse notes"):
        onenote_counts = lp_knowledgebase.summarize_onenote_journal_entries(
            resolved_inputs.onenote_journal, start_month, resolved_end_month
        )
        substance_headings = lp_knowledgebase.summarize_substance_log_headings(
            resolved_inputs.substance_log, start_month, resolved_end_month
        )

    with _stage("Parse git activity"):
        git_repos = lp_gitstats.active_repo_paths()
        git_commit_counts, git_commit_repos = lp_gitstats.summarize_commit_activity(
            start_month=start_month,
            end_month=resolved_end_month,
            repos=git_repos,
        )

    with _stage("Discover Google Takeout archives"):
        resolved_takeout_root = resolved_inputs.takeout_root or (cfg.exports_root / "google" / "raw" / "takeout")
        takeout_paths_used = lp_takeout.resolve_archives(
            explicit_seeds=list(resolved_inputs.takeout_paths),
            root=resolved_takeout_root,
        )
        if not takeout_paths_used:
            raise FileNotFoundError(
                f"No Google Takeout archives found (expected takeout*.tgz under {resolved_takeout_root})."
            )

    with _stage(f"Parse Google Takeout ({len(takeout_paths_used)} archives)"):
        takeout_bundle = lp_takeout.parse_life_takeouts(
            takeout_paths_used,
            start_month=start_month,
            end_month=resolved_end_month,
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

    youtube_oembed_by_id = lp_takeout.load_youtube_oembed_cache(resolved_inputs.youtube_oembed_cache)
    context_months, context_window = build_recent_context_summaries(months)

    monthly: dict[str, dict] = {}
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
        monthly[month] = build_month_summary(
            output=build_output_summary(
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
            work=build_work_summary(
                month,
                git_commit_counts=git_commit_counts,
                git_commit_repos=git_commit_repos,
                chat_session_count=context_months[month].chat_session_count if month in context_months else 0,
                chat_work_events=dict(context_months[month].chat_work_events) if month in context_months else {},
            ),
            intake=build_intake_summary(
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
            mail=build_mail_summary(
                month,
                gmail_counts=gmail_counts,
                gmail_from_domains=gmail_from_domains,
                gmail_subject_tokens=gmail_subject_tokens,
            ),
            location=build_location_summary(
                month,
                location_records=location_records,
                semantic_place_visits=semantic_place_visits,
                semantic_activity_segments=semantic_activity_segments,
                semantic_top_places=semantic_top_places,
                semantic_top_activities=semantic_top_activities,
            ),
            money=build_money_summary(
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
            health=build_health_summary(
                month,
                sleep_sessions=sleep_sessions,
                sleep_total_hours=sleep_total_hours,
                weights=weights,
            ),
            notes=build_notes_summary(
                month,
                onenote_counts=onenote_counts,
                substance_headings=substance_headings,
            ),
            context=context_months.get(month),
        ).to_dict()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "range": {"start_month": start_month, "end_month": resolved_end_month},
        "sources": {
            "reddit_comments_csv": str(resolved_inputs.reddit_comments) if resolved_inputs.reddit_comments else None,
            "reddit_posts_csv": str(resolved_inputs.reddit_posts) if resolved_inputs.reddit_posts else None,
            "reddit_messages_csv": str(resolved_inputs.reddit_messages) if resolved_inputs.reddit_messages else None,
            "wykop_link_comments_jsonl": str(resolved_inputs.wykop_link_comments),
            "wykop_entries_jsonl": str(resolved_inputs.wykop_entries),
            "wykop_entry_comments_jsonl": str(resolved_inputs.wykop_entry_comments),
            "webhistory_source": webhistory_source,
            "webhistory_ndjson": str(resolved_inputs.webhistory),
            "webhistory_gestalt_dir": (
                str(resolved_inputs.webhistory_gestalt_dir)
                if resolved_inputs.webhistory_gestalt_dir is not None
                else None
            ),
            "google_takeouts": [str(path) for path in takeout_paths_used],
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
            "youtube_oembed_cache_jsonl": (
                str(resolved_inputs.youtube_oembed_cache) if resolved_inputs.youtube_oembed_cache.exists() else None
            ),
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
            "finance_ledger": str(resolved_inputs.ledger),
            "finance_revolut_annotated": str(resolved_inputs.revolut_annotated),
            "finance_revolut_recent": str(resolved_inputs.revolut_recent),
            "finance_mbank_personal": str(resolved_inputs.mbank_personal),
            "finance_mbank_business": str(resolved_inputs.mbank_business),
            "samsung_health_export": str(resolved_inputs.samsung_health_export),
            "onenote_journal": str(resolved_inputs.onenote_journal),
            "substance_log": str(resolved_inputs.substance_log),
            "raindrop_bookmarks": str(resolved_inputs.raindrop_bookmarks),
            "goodreads_library_csv": str(resolved_inputs.goodreads_library),
            "spotify_dir": str(resolved_spotify_dir),
            "git_repos": [str(path) for path in git_repos],
            "recent_context_window": context_window if context_window.get("month_count") else None,
        },
        "output_path": str(output),
        "months": monthly,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown(payload), encoding="utf-8")
    if markdown_output_dir is not None:
        markdown_output_dir.mkdir(parents=True, exist_ok=True)
        years = sorted({month.split("-", 1)[0] for month in months})
        index_lines = [
            f"# Life timeline drilldowns ({start_month} → {resolved_end_month})",
            "",
            f"Generated: `{payload.get('generated_at')}`",
            f"Backing JSON: `{output}`",
            "",
            "## Years",
            "",
            *[f"- `{year}.md`" for year in years],
            "",
        ]
        (markdown_output_dir / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
        for year in years:
            year_months = {month: payload["months"][month] for month in payload["months"] if month.startswith(f"{year}-")}
            if not year_months:
                continue
            year_payload = {
                "generated_at": payload.get("generated_at"),
                "range": {"start_month": min(year_months.keys()), "end_month": max(year_months.keys())},
                "output_path": str(output),
                "months": year_months,
            }
            (markdown_output_dir / f"{year}.md").write_text(render_markdown(year_payload), encoding="utf-8")

    artifact_paths: dict[str, Path] = {"output": output, "youtube_oembed_cache": resolved_inputs.youtube_oembed_cache}
    if markdown_output is not None:
        artifact_paths["markdown_output"] = markdown_output
    if markdown_output_dir is not None:
        artifact_paths["markdown_output_dir"] = markdown_output_dir

    return LifeRangeResult(
        output=output,
        start_month=start_month,
        end_month=resolved_end_month,
        month_count=len(payload.get("months", {})),
        artifact_paths=artifact_paths,
    )
