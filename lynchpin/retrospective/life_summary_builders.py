from __future__ import annotations

from collections import Counter
from typing import Mapping, Optional, Sequence

from .life_summary_models import (
    LifeMonthContextSummary,
    LifeMonthHealthSummary,
    LifeMonthIntakeSummary,
    LifeMonthLocationSummary,
    LifeMonthMailSummary,
    LifeMonthMoneySummary,
    LifeMonthNotesSummary,
    LifeMonthOutputSummary,
    LifeMonthSummary,
    LifeMonthWorkSummary,
)


def build_month_summary(
    *,
    output: LifeMonthOutputSummary,
    work: LifeMonthWorkSummary,
    intake: LifeMonthIntakeSummary,
    mail: LifeMonthMailSummary,
    location: LifeMonthLocationSummary,
    money: LifeMonthMoneySummary,
    health: LifeMonthHealthSummary,
    notes: LifeMonthNotesSummary,
    context: Optional[LifeMonthContextSummary],
) -> LifeMonthSummary:
    return LifeMonthSummary(
        output=output,
        work=work,
        intake=intake,
        mail=mail,
        location=location,
        money=money,
        health=health,
        notes=notes,
        context=context,
    )


def build_work_summary(
    month: str,
    *,
    git_commit_counts: Mapping[str, int],
    git_commit_repos: Mapping[str, Counter[str]],
    chat_session_count: int = 0,
    chat_work_events: Optional[Mapping[str, int]] = None,
) -> LifeMonthWorkSummary:
    return LifeMonthWorkSummary(
        git_commits=git_commit_counts.get(month, 0),
        git_top_repos=list(git_commit_repos.get(month, Counter()).most_common(10)),
        chat_session_count=chat_session_count,
        chat_work_events=dict(chat_work_events) if chat_work_events else {},
    )


def build_output_summary(
    month: str,
    *,
    reddit_comment_counts: Mapping[str, int],
    reddit_post_counts: Mapping[str, int],
    reddit_message_counts: Mapping[str, int],
    wykop_link_counts: Mapping[str, int],
    wykop_entry_counts: Mapping[str, int],
    wykop_entry_comment_counts: Mapping[str, int],
    reddit_comment_subs: Mapping[str, Counter[str]],
    wykop_link_tags: Mapping[str, Counter[str]],
    wykop_entry_tags: Mapping[str, Counter[str]],
    topic_tokens: Counter[str],
) -> LifeMonthOutputSummary:
    return LifeMonthOutputSummary(
        reddit_comments=reddit_comment_counts.get(month, 0),
        reddit_posts=reddit_post_counts.get(month, 0),
        reddit_messages=reddit_message_counts.get(month, 0),
        wykop_link_comments=wykop_link_counts.get(month, 0),
        wykop_entries=wykop_entry_counts.get(month, 0),
        wykop_entry_comments=wykop_entry_comment_counts.get(month, 0),
        reddit_top_subs=list(reddit_comment_subs.get(month, Counter()).most_common(15)),
        wykop_top_tags=list(wykop_link_tags.get(month, Counter()).most_common(15)),
        wykop_entries_top_tags=list(wykop_entry_tags.get(month, Counter()).most_common(15)),
        output_top_topic_tokens=list(topic_tokens.most_common(20)),
    )


def build_intake_summary(
    month: str,
    *,
    web_counts: Mapping[str, int],
    web_domains: Mapping[str, Counter[str]],
    web_reddit_subs: Mapping[str, Counter[str]],
    web_title_tokens: Mapping[str, Counter[str]],
    raindrop_counts: Mapping[str, int],
    goodreads_read_counts: Mapping[str, int],
    goodreads_added_counts: Mapping[str, int],
    goodreads_authors_read: Mapping[str, Counter[str]],
    goodreads_titles_read: Mapping[str, Counter[str]],
    google_search_counts: Mapping[str, int],
    google_search_tokens: Mapping[str, Counter[str]],
    google_search_phrases: Mapping[str, Counter[str]],
    youtube_watch_counts: Mapping[str, int],
    youtube_search_counts: Mapping[str, int],
    youtube_search_tokens: Mapping[str, Counter[str]],
    youtube_search_phrases: Mapping[str, Counter[str]],
    youtube_watch_history_counts: Mapping[str, int],
    yt_watch_history_video_id_top: Sequence[tuple[str, int]],
    yt_watch_history_channels: Counter[str],
    yt_watch_history_tokens: Counter[str],
    yt_watch_history_titles: Counter[str],
    youtube_search_history_counts: Mapping[str, int],
    youtube_search_history_tokens: Mapping[str, Counter[str]],
    youtube_search_history_phrases: Mapping[str, Counter[str]],
    chrome_counts: Mapping[str, int],
    chrome_history_counts: Mapping[str, int],
    chrome_history_domains: Mapping[str, Counter[str]],
    chrome_history_reddit_subs: Mapping[str, Counter[str]],
    chrome_history_title_tokens: Mapping[str, Counter[str]],
    maps_counts: Mapping[str, int],
    maps_tokens: Mapping[str, Counter[str]],
    maps_phrases: Mapping[str, Counter[str]],
    image_search_counts: Mapping[str, int],
    image_search_tokens: Mapping[str, Counter[str]],
    image_search_phrases: Mapping[str, Counter[str]],
    play_store_counts: Mapping[str, int],
    play_store_tokens: Mapping[str, Counter[str]],
    play_store_phrases: Mapping[str, Counter[str]],
    video_search_counts: Mapping[str, int],
    video_search_tokens: Mapping[str, Counter[str]],
    video_search_phrases: Mapping[str, Counter[str]],
    shopping_counts: Mapping[str, int],
    shopping_tokens: Mapping[str, Counter[str]],
    shopping_phrases: Mapping[str, Counter[str]],
    travel_counts: Mapping[str, int],
    travel_tokens: Mapping[str, Counter[str]],
    travel_phrases: Mapping[str, Counter[str]],
    myactivity_other: Counter[str],
    spotify_hours: Mapping[str, float],
    spotify_top_artists: Sequence[str],
    spotify_top_tracks: Sequence[str],
    intake_topic_tokens: Counter[str],
) -> LifeMonthIntakeSummary:
    return LifeMonthIntakeSummary(
        webhistory_events=web_counts.get(month, 0),
        webhistory_top_domains=list(web_domains.get(month, Counter()).most_common(15)),
        webhistory_top_reddit_subs=list(web_reddit_subs.get(month, Counter()).most_common(15)),
        webhistory_top_title_tokens=list(web_title_tokens.get(month, Counter()).most_common(15)),
        raindrop_bookmarks=raindrop_counts.get(month, 0),
        goodreads_books_read=goodreads_read_counts.get(month, 0),
        goodreads_books_added=goodreads_added_counts.get(month, 0),
        goodreads_top_authors_read=list(goodreads_authors_read.get(month, Counter()).most_common(12)),
        goodreads_top_titles_read=list(goodreads_titles_read.get(month, Counter()).most_common(12)),
        google_searches=google_search_counts.get(month, 0),
        google_search_top_tokens=list(google_search_tokens.get(month, Counter()).most_common(15)),
        google_search_top_queries=list(google_search_phrases.get(month, Counter()).most_common(15)),
        youtube_watch=youtube_watch_counts.get(month, 0),
        youtube_searches=youtube_search_counts.get(month, 0),
        youtube_search_top_tokens=list(youtube_search_tokens.get(month, Counter()).most_common(15)),
        youtube_search_top_queries=list(youtube_search_phrases.get(month, Counter()).most_common(15)),
        youtube_watch_history=youtube_watch_history_counts.get(month, 0),
        youtube_watch_history_top_video_ids=list(yt_watch_history_video_id_top),
        youtube_watch_history_top_channels=list(yt_watch_history_channels.most_common(15)),
        youtube_watch_history_top_tokens=list(yt_watch_history_tokens.most_common(15)),
        youtube_watch_history_top_titles=list(yt_watch_history_titles.most_common(15)),
        youtube_search_history=youtube_search_history_counts.get(month, 0),
        youtube_search_history_top_tokens=list(youtube_search_history_tokens.get(month, Counter()).most_common(15)),
        youtube_search_history_top_queries=list(youtube_search_history_phrases.get(month, Counter()).most_common(15)),
        chrome_myactivity=chrome_counts.get(month, 0),
        chrome_history_events=chrome_history_counts.get(month, 0),
        chrome_history_top_domains=list(chrome_history_domains.get(month, Counter()).most_common(15)),
        chrome_history_top_reddit_subs=list(chrome_history_reddit_subs.get(month, Counter()).most_common(15)),
        chrome_history_top_title_tokens=list(chrome_history_title_tokens.get(month, Counter()).most_common(15)),
        maps_myactivity=maps_counts.get(month, 0),
        maps_search_top_tokens=list(maps_tokens.get(month, Counter()).most_common(15)),
        maps_search_top_queries=list(maps_phrases.get(month, Counter()).most_common(15)),
        image_search_myactivity=image_search_counts.get(month, 0),
        image_search_top_tokens=list(image_search_tokens.get(month, Counter()).most_common(15)),
        image_search_top_queries=list(image_search_phrases.get(month, Counter()).most_common(15)),
        play_store_myactivity=play_store_counts.get(month, 0),
        play_store_top_tokens=list(play_store_tokens.get(month, Counter()).most_common(15)),
        play_store_top_queries=list(play_store_phrases.get(month, Counter()).most_common(15)),
        video_search_myactivity=video_search_counts.get(month, 0),
        video_search_top_tokens=list(video_search_tokens.get(month, Counter()).most_common(15)),
        video_search_top_queries=list(video_search_phrases.get(month, Counter()).most_common(15)),
        shopping_myactivity=shopping_counts.get(month, 0),
        shopping_top_tokens=list(shopping_tokens.get(month, Counter()).most_common(15)),
        shopping_top_queries=list(shopping_phrases.get(month, Counter()).most_common(15)),
        travel_myactivity=travel_counts.get(month, 0),
        travel_top_tokens=list(travel_tokens.get(month, Counter()).most_common(15)),
        travel_top_queries=list(travel_phrases.get(month, Counter()).most_common(15)),
        myactivity_other_categories=list(myactivity_other.most_common()),
        spotify_hours=round(spotify_hours.get(month, 0.0), 1) if month in spotify_hours else None,
        spotify_top_artists=list(spotify_top_artists),
        spotify_top_tracks=list(spotify_top_tracks),
        intake_top_topic_tokens=list(intake_topic_tokens.most_common(20)),
    )


def build_mail_summary(
    month: str,
    *,
    gmail_counts: Mapping[str, int],
    gmail_from_domains: Mapping[str, Counter[str]],
    gmail_subject_tokens: Mapping[str, Counter[str]],
) -> LifeMonthMailSummary:
    return LifeMonthMailSummary(
        gmail_messages=gmail_counts.get(month, 0),
        gmail_top_from_domains=list(gmail_from_domains.get(month, Counter()).most_common(12)),
        gmail_top_subject_tokens=list(gmail_subject_tokens.get(month, Counter()).most_common(12)),
    )


def build_location_summary(
    month: str,
    *,
    location_records: Mapping[str, int],
    semantic_place_visits: Mapping[str, int],
    semantic_activity_segments: Mapping[str, int],
    semantic_top_places: Mapping[str, Counter[str]],
    semantic_top_activities: Mapping[str, Counter[str]],
) -> LifeMonthLocationSummary:
    return LifeMonthLocationSummary(
        records=location_records.get(month, 0),
        semantic_place_visits=semantic_place_visits.get(month, 0),
        semantic_activity_segments=semantic_activity_segments.get(month, 0),
        semantic_top_places=list(semantic_top_places.get(month, Counter()).most_common(12)),
        semantic_top_activities=list(semantic_top_activities.get(month, Counter()).most_common(12)),
    )


def build_money_summary(
    month: str,
    *,
    ledger_expenses: Mapping[str, float],
    revolut_out_annotated: Mapping[str, float],
    revolut_out_recent: Mapping[str, float],
    revolut_in_annotated: Mapping[str, float],
    revolut_in_recent: Mapping[str, float],
    mbank_personal_out: Mapping[str, float],
    mbank_personal_in: Mapping[str, float],
    mbank_business_out: Mapping[str, float],
    mbank_business_in: Mapping[str, float],
) -> LifeMonthMoneySummary:
    return LifeMonthMoneySummary(
        ledger_expenses_pln=round(ledger_expenses.get(month, 0.0), 2) if month in ledger_expenses else None,
        revolut_out_pln=round(revolut_out_annotated.get(month, 0.0) + revolut_out_recent.get(month, 0.0), 2),
        revolut_in_pln=round(revolut_in_annotated.get(month, 0.0) + revolut_in_recent.get(month, 0.0), 2),
        mbank_personal_out_pln=round(mbank_personal_out.get(month, 0.0), 2),
        mbank_personal_in_pln=round(mbank_personal_in.get(month, 0.0), 2),
        mbank_business_out_pln=round(mbank_business_out.get(month, 0.0), 2),
        mbank_business_in_pln=round(mbank_business_in.get(month, 0.0), 2),
    )


def build_notes_summary(
    month: str,
    *,
    onenote_counts: Mapping[str, int],
    substance_headings: Mapping[str, int],
) -> LifeMonthNotesSummary:
    return LifeMonthNotesSummary(
        onenote_journal_entries=onenote_counts.get(month, 0),
        substance_log_headings=substance_headings.get(month, 0),
    )


def build_health_summary(
    month: str,
    *,
    sleep_sessions: Mapping[str, int],
    sleep_total_hours: Mapping[str, float],
    weights: Sequence[float],
) -> LifeMonthHealthSummary:
    sleep_n = sleep_sessions.get(month, 0)
    sleep_total = sleep_total_hours.get(month, 0.0)
    return LifeMonthHealthSummary(
        sleep_sessions=sleep_n,
        sleep_total_h=round(sleep_total, 2) if sleep_n else None,
        sleep_avg_h=round(sleep_total / sleep_n, 2) if sleep_n else None,
        weight_n=len(weights),
        weight_min=min(weights) if weights else None,
        weight_max=max(weights) if weights else None,
    )
