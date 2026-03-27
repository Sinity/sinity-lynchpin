from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from lynchpin.sources.exports import spotify as lp_spotify
from lynchpin.sources.exports import takeout_common as lp_takeout_common
from lynchpin.sources.exports import takeout_youtube as lp_takeout_youtube

from .life_range_models import LifeRangeEvidence, LifeRangeInputs
from .life_summary_builders import (
    build_health_summary,
    build_intake_summary,
    build_location_summary,
    build_mail_summary,
    build_month_summary,
    build_money_summary,
    build_notes_summary,
    build_output_summary,
    build_work_summary,
)


def build_life_range_payload(
    *,
    start_month: str,
    end_month: str,
    output: Path,
    months: list[str],
    evidence: LifeRangeEvidence,
    inputs: LifeRangeInputs,
) -> dict[str, object]:
    monthly: dict[str, dict] = {}
    for month in months:
        yt_video_ids, yt_titles, yt_channels, yt_tokens = lp_takeout_youtube.summarize_youtube_watch_history_month(
            evidence.takeout.youtube_watch_history_video_ids.get(month, Counter()),
            evidence.takeout.youtube_watch_history_titles.get(month, Counter()),
            evidence.takeout.youtube_watch_history_channels.get(month, Counter()),
            takeout_titles=evidence.takeout.youtube_video_titles,
            oembed_cache=evidence.youtube_oembed_by_id,
            tokenize_text=lp_takeout_common.tokenize_topic,
        )
        monthly[month] = build_month_summary(
            output=build_output_summary(
                month,
                reddit_comment_counts=evidence.reddit.comment_counts,
                reddit_post_counts=evidence.reddit.post_counts,
                reddit_message_counts=evidence.reddit.message_counts,
                wykop_link_counts=evidence.wykop.link_comment_counts,
                wykop_entry_counts=evidence.wykop.entry_counts,
                wykop_entry_comment_counts=evidence.wykop.entry_comment_counts,
                reddit_comment_subs=evidence.reddit.comment_subreddits,
                wykop_link_tags=evidence.wykop.link_comment_tags,
                wykop_entry_tags=evidence.wykop.entry_tags,
                topic_tokens=_build_output_topic_tokens(month, evidence),
            ),
            work=build_work_summary(
                month,
                git_commit_counts=evidence.git.commit_counts,
                git_commit_repos=evidence.git.commit_repos,
                chat_session_count=evidence.context_months[month].chat_session_count if month in evidence.context_months else 0,
                chat_work_events=dict(evidence.context_months[month].chat_work_events)
                if month in evidence.context_months
                else {},
            ),
            intake=build_intake_summary(
                month,
                web_counts=evidence.webhistory.counts,
                web_domains=evidence.webhistory.domains,
                web_reddit_subs=evidence.webhistory.reddit_subs,
                web_title_tokens=evidence.webhistory.title_tokens,
                raindrop_counts=evidence.raindrop_counts,
                goodreads_read_counts=evidence.goodreads.read_counts,
                goodreads_added_counts=evidence.goodreads.added_counts,
                goodreads_authors_read=evidence.goodreads.authors_read,
                goodreads_titles_read=evidence.goodreads.titles_read,
                google_search_counts=evidence.takeout.google_search_counts,
                google_search_tokens=evidence.takeout.google_search_tokens,
                google_search_phrases=evidence.takeout.google_search_phrases,
                youtube_watch_counts=evidence.takeout.youtube_watch_counts,
                youtube_search_counts=evidence.takeout.youtube_search_counts,
                youtube_search_tokens=evidence.takeout.youtube_search_tokens,
                youtube_search_phrases=evidence.takeout.youtube_search_phrases,
                youtube_watch_history_counts=evidence.takeout.youtube_watch_history_counts,
                yt_watch_history_video_id_top=yt_video_ids,
                yt_watch_history_channels=yt_channels,
                yt_watch_history_tokens=yt_tokens,
                yt_watch_history_titles=yt_titles,
                youtube_search_history_counts=evidence.takeout.youtube_search_history_counts,
                youtube_search_history_tokens=evidence.takeout.youtube_search_history_tokens,
                youtube_search_history_phrases=evidence.takeout.youtube_search_history_phrases,
                chrome_counts=evidence.takeout.chrome_counts,
                chrome_history_counts=evidence.takeout.chrome_history_counts,
                chrome_history_domains=evidence.takeout.chrome_history_domains,
                chrome_history_reddit_subs=evidence.takeout.chrome_history_reddit_subs,
                chrome_history_title_tokens=evidence.takeout.chrome_history_title_tokens,
                maps_counts=evidence.takeout.maps_counts,
                maps_tokens=evidence.takeout.maps_tokens,
                maps_phrases=evidence.takeout.maps_phrases,
                image_search_counts=evidence.takeout.image_search_counts,
                image_search_tokens=evidence.takeout.image_search_tokens,
                image_search_phrases=evidence.takeout.image_search_phrases,
                play_store_counts=evidence.takeout.play_store_counts,
                play_store_tokens=evidence.takeout.play_store_tokens,
                play_store_phrases=evidence.takeout.play_store_phrases,
                video_search_counts=evidence.takeout.video_search_counts,
                video_search_tokens=evidence.takeout.video_search_tokens,
                video_search_phrases=evidence.takeout.video_search_phrases,
                shopping_counts=evidence.takeout.shopping_counts,
                shopping_tokens=evidence.takeout.shopping_tokens,
                shopping_phrases=evidence.takeout.shopping_phrases,
                travel_counts=evidence.takeout.travel_counts,
                travel_tokens=evidence.takeout.travel_tokens,
                travel_phrases=evidence.takeout.travel_phrases,
                myactivity_other=evidence.takeout.myactivity_other_counts.get(month, Counter()),
                spotify_hours=evidence.spotify.hours,
                spotify_top_artists=lp_spotify.top_names(evidence.spotify.artists, month, limit=3),
                spotify_top_tracks=lp_spotify.top_names(evidence.spotify.tracks, month, limit=3),
                intake_topic_tokens=_build_intake_topic_tokens(month, evidence, yt_tokens),
            ),
            mail=build_mail_summary(
                month,
                gmail_counts=evidence.takeout.gmail_counts,
                gmail_from_domains=evidence.takeout.gmail_from_domains,
                gmail_subject_tokens=evidence.takeout.gmail_subject_tokens,
            ),
            location=build_location_summary(
                month,
                location_records=evidence.takeout.location_records,
                semantic_place_visits=evidence.takeout.semantic_place_visits,
                semantic_activity_segments=evidence.takeout.semantic_activity_segments,
                semantic_top_places=evidence.takeout.semantic_top_places,
                semantic_top_activities=evidence.takeout.semantic_top_activities,
            ),
            money=build_money_summary(
                month,
                ledger_expenses=evidence.finance.ledger_expenses,
                revolut_out_annotated=evidence.finance.revolut_out_annotated,
                revolut_out_recent=evidence.finance.revolut_out_recent,
                revolut_in_annotated=evidence.finance.revolut_in_annotated,
                revolut_in_recent=evidence.finance.revolut_in_recent,
                mbank_personal_out=evidence.finance.mbank_personal_out,
                mbank_personal_in=evidence.finance.mbank_personal_in,
                mbank_business_out=evidence.finance.mbank_business_out,
                mbank_business_in=evidence.finance.mbank_business_in,
            ),
            health=build_health_summary(
                month,
                sleep_sessions=evidence.health.sleep_sessions,
                sleep_total_hours=evidence.health.sleep_total_hours,
                weights=evidence.health.weight_values.get(month, []),
            ),
            notes=build_notes_summary(
                month,
                onenote_counts=evidence.notes.onenote_counts,
                substance_headings=evidence.notes.substance_headings,
            ),
            context=evidence.context_months.get(month),
        ).to_dict()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "range": {"start_month": start_month, "end_month": end_month},
        "sources": _build_sources_payload(output=output, inputs=inputs, evidence=evidence),
        "output_path": str(output),
        "months": monthly,
    }


def _build_output_topic_tokens(month: str, evidence: LifeRangeEvidence) -> Counter[str]:
    topic_tokens = Counter()
    topic_tokens.update(evidence.reddit.comment_tokens.get(month, Counter()))
    topic_tokens.update(evidence.wykop.link_comment_tokens.get(month, Counter()))
    topic_tokens.update(evidence.wykop.entry_tokens.get(month, Counter()))
    topic_tokens.update(evidence.wykop.entry_comment_tokens.get(month, Counter()))
    return topic_tokens


def _build_intake_topic_tokens(
    month: str,
    evidence: LifeRangeEvidence,
    yt_watch_history_tokens: Counter[str],
) -> Counter[str]:
    intake_topic_tokens = Counter()
    intake_topic_tokens.update(evidence.webhistory.title_tokens.get(month, Counter()))
    intake_topic_tokens.update(evidence.takeout.chrome_history_title_tokens.get(month, Counter()))
    intake_topic_tokens.update(yt_watch_history_tokens)
    intake_topic_tokens.update(
        lp_takeout_youtube.phrase_topic_tokens(
            evidence.takeout.google_search_phrases.get(month, Counter()),
            tokenize_text=lp_takeout_common.tokenize_topic,
        )
    )
    intake_topic_tokens.update(
        lp_takeout_youtube.phrase_topic_tokens(
            evidence.takeout.youtube_search_phrases.get(month, Counter()),
            tokenize_text=lp_takeout_common.tokenize_topic,
        )
    )
    intake_topic_tokens.update(
        lp_takeout_youtube.phrase_topic_tokens(
            evidence.takeout.youtube_search_history_phrases.get(month, Counter()),
            tokenize_text=lp_takeout_common.tokenize_topic,
        )
    )
    return intake_topic_tokens


def _build_sources_payload(
    *,
    output: Path,
    inputs: LifeRangeInputs,
    evidence: LifeRangeEvidence,
) -> dict[str, object]:
    return {
        "reddit_comments_csv": str(inputs.reddit_comments) if inputs.reddit_comments else None,
        "reddit_posts_csv": str(inputs.reddit_posts) if inputs.reddit_posts else None,
        "reddit_messages_csv": str(inputs.reddit_messages) if inputs.reddit_messages else None,
        "wykop_link_comments_jsonl": str(inputs.wykop_link_comments),
        "wykop_entries_jsonl": str(inputs.wykop_entries),
        "wykop_entry_comments_jsonl": str(inputs.wykop_entry_comments),
        "webhistory_source": evidence.webhistory.source,
        "webhistory_ndjson": str(inputs.webhistory),
        "webhistory_gestalt_dir": str(inputs.webhistory_gestalt_dir) if inputs.webhistory_gestalt_dir is not None else None,
        "google_takeouts": [str(path) for path in evidence.takeout_paths],
        "chrome_history_json": (
            f"{evidence.takeout.chrome_history_takeout_path}:{'Takeout/Chrome/History.json'}"
            if evidence.takeout.chrome_history_takeout_path
            else None
        ),
        "youtube_watch_history_html": "Takeout/YouTube and YouTube Music/history/watch-history.html",
        "youtube_search_history_html": "Takeout/YouTube and YouTube Music/history/search-history.html",
        "youtube_video_texts_csv": (
            f"{evidence.takeout.youtube_video_texts_takeout_path}:{'Takeout/YouTube and YouTube Music/video metadata/video texts.csv'}"
            if evidence.takeout.youtube_video_texts_takeout_path
            else None
        ),
        "youtube_oembed_cache_jsonl": str(inputs.youtube_oembed_cache) if inputs.youtube_oembed_cache.exists() else None,
        "gmail_mbox": (
            f"{evidence.takeout.gmail_takeout_path}:{'Takeout/Mail/All mail Including Spam and Trash.mbox'}"
            if evidence.takeout.gmail_takeout_path
            else None
        ),
        "location_records": (
            f"{evidence.takeout.location_takeout_path}:{'Takeout/Location History/Records.json'}"
            if evidence.takeout.location_takeout_path
            else None
        ),
        "semantic_location_history": (
            f"{evidence.takeout.location_takeout_path}:Takeout/Location History/Semantic Location History/"
            if evidence.takeout.location_takeout_path
            else None
        ),
        "finance_ledger": str(inputs.ledger),
        "finance_revolut_annotated": str(inputs.revolut_annotated),
        "finance_revolut_recent": str(inputs.revolut_recent),
        "finance_mbank_personal": str(inputs.mbank_personal),
        "finance_mbank_business": str(inputs.mbank_business),
        "samsung_health_export": str(inputs.samsung_health_export),
        "onenote_journal": str(inputs.onenote_journal),
        "substance_log": str(inputs.substance_log),
        "raindrop_bookmarks": str(inputs.raindrop_bookmarks),
        "goodreads_library_csv": str(inputs.goodreads_library),
        "spotify_dir": str(evidence.resolved_spotify_dir) if evidence.resolved_spotify_dir is not None else None,
        "git_repos": [str(path) for path in evidence.git.repos],
        "recent_context_window": evidence.context_window if evidence.context_window.get("month_count") else None,
        "output_path": str(output),
    }
