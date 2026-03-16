from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional, Sequence

from ..trajectory import day as trajectory_day
from ..trajectory import period as trajectory_period


RECENT_TRAJECTORY_LOOKBACK_DAYS = 62


@dataclass(frozen=True)
class LifeMonthWorkSummary:
    git_commits: int
    git_top_repos: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthHealthSummary:
    sleep_sessions: int
    sleep_total_h: Optional[float]
    sleep_avg_h: Optional[float]
    weight_n: int
    weight_min: Optional[float]
    weight_max: Optional[float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthTrajectorySummary:
    start_date: str
    end_date: str
    days: int
    active_hours: float
    recovery_hours: float
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    dominant_modes: list[tuple[str, float]]
    dominant_projects: list[tuple[str, float]]
    source_counts: dict[str, int]
    coverage: dict[str, object]
    highlights: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthSummary:
    output: "LifeMonthOutputSummary"
    work: LifeMonthWorkSummary
    intake: "LifeMonthIntakeSummary"
    mail: "LifeMonthMailSummary"
    location: "LifeMonthLocationSummary"
    money: "LifeMonthMoneySummary"
    health: LifeMonthHealthSummary
    notes: "LifeMonthNotesSummary"
    trajectory: Optional[LifeMonthTrajectorySummary] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "output": self.output.to_dict(),
            "work": self.work.to_dict(),
            "intake": self.intake.to_dict(),
            "mail": self.mail.to_dict(),
            "location": self.location.to_dict(),
            "money": self.money.to_dict(),
            "health": self.health.to_dict(),
            "trajectory": self.trajectory.to_dict() if self.trajectory is not None else {},
            "notes": self.notes.to_dict(),
        }


@dataclass(frozen=True)
class LifeMonthOutputSummary:
    reddit_comments: int
    reddit_posts: int
    reddit_messages: int
    wykop_link_comments: int
    wykop_entries: int
    wykop_entry_comments: int
    reddit_top_subs: list[tuple[str, int]]
    wykop_top_tags: list[tuple[str, int]]
    wykop_entries_top_tags: list[tuple[str, int]]
    output_top_topic_tokens: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthIntakeSummary:
    webhistory_events: int
    webhistory_top_domains: list[tuple[str, int]]
    webhistory_top_reddit_subs: list[tuple[str, int]]
    webhistory_top_title_tokens: list[tuple[str, int]]
    raindrop_bookmarks: int
    goodreads_books_read: int
    goodreads_books_added: int
    goodreads_top_authors_read: list[tuple[str, int]]
    goodreads_top_titles_read: list[tuple[str, int]]
    google_searches: int
    google_search_top_tokens: list[tuple[str, int]]
    google_search_top_queries: list[tuple[str, int]]
    youtube_watch: int
    youtube_searches: int
    youtube_search_top_tokens: list[tuple[str, int]]
    youtube_search_top_queries: list[tuple[str, int]]
    youtube_watch_history: int
    youtube_watch_history_top_video_ids: list[tuple[str, int]]
    youtube_watch_history_top_channels: list[tuple[str, int]]
    youtube_watch_history_top_tokens: list[tuple[str, int]]
    youtube_watch_history_top_titles: list[tuple[str, int]]
    youtube_search_history: int
    youtube_search_history_top_tokens: list[tuple[str, int]]
    youtube_search_history_top_queries: list[tuple[str, int]]
    chrome_myactivity: int
    chrome_history_events: int
    chrome_history_top_domains: list[tuple[str, int]]
    chrome_history_top_reddit_subs: list[tuple[str, int]]
    chrome_history_top_title_tokens: list[tuple[str, int]]
    maps_myactivity: int
    maps_search_top_tokens: list[tuple[str, int]]
    maps_search_top_queries: list[tuple[str, int]]
    image_search_myactivity: int
    image_search_top_tokens: list[tuple[str, int]]
    image_search_top_queries: list[tuple[str, int]]
    play_store_myactivity: int
    play_store_top_tokens: list[tuple[str, int]]
    play_store_top_queries: list[tuple[str, int]]
    video_search_myactivity: int
    video_search_top_tokens: list[tuple[str, int]]
    video_search_top_queries: list[tuple[str, int]]
    shopping_myactivity: int
    shopping_top_tokens: list[tuple[str, int]]
    shopping_top_queries: list[tuple[str, int]]
    travel_myactivity: int
    travel_top_tokens: list[tuple[str, int]]
    travel_top_queries: list[tuple[str, int]]
    myactivity_other_categories: list[tuple[str, int]]
    spotify_hours: Optional[float]
    spotify_top_artists: list[str]
    spotify_top_tracks: list[str]
    intake_top_topic_tokens: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthMailSummary:
    gmail_messages: int
    gmail_top_from_domains: list[tuple[str, int]]
    gmail_top_subject_tokens: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthLocationSummary:
    records: int
    semantic_place_visits: int
    semantic_activity_segments: int
    semantic_top_places: list[tuple[str, int]]
    semantic_top_activities: list[tuple[str, int]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthMoneySummary:
    ledger_expenses_pln: Optional[float]
    revolut_out_pln: float
    revolut_in_pln: float
    mbank_personal_out_pln: float
    mbank_personal_in_pln: float
    mbank_business_out_pln: float
    mbank_business_in_pln: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifeMonthNotesSummary:
    onenote_journal_entries: int
    substance_log_headings: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


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
    trajectory: Optional[LifeMonthTrajectorySummary],
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
        trajectory=trajectory,
    )


def build_work_summary(
    month: str,
    *,
    git_commit_counts: Mapping[str, int],
    git_commit_repos: Mapping[str, Counter[str]],
) -> LifeMonthWorkSummary:
    return LifeMonthWorkSummary(
        git_commits=git_commit_counts.get(month, 0),
        git_top_repos=list(git_commit_repos.get(month, Counter()).most_common(10)),
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


def build_recent_trajectory_summaries(
    months: Sequence[str],
    *,
    lookback_days: int = RECENT_TRAJECTORY_LOOKBACK_DAYS,
    now: Optional[datetime] = None,
) -> tuple[dict[str, LifeMonthTrajectorySummary], dict[str, object]]:
    if not months:
        return {}, {"lookback_days": lookback_days, "month_count": 0}

    tz = datetime.now().astimezone().tzinfo or timezone.utc
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    recent_floor = (current - timedelta(days=lookback_days)).date().strftime("%Y-%m")
    target_months = [month for month in months if month >= recent_floor]
    if not target_months:
        return {}, {"lookback_days": lookback_days, "month_count": 0}

    start_dt = _month_start(min(target_months), tz)
    end_dt = min(_month_after(max(target_months), tz), current)
    if start_dt >= end_dt:
        return {}, {"lookback_days": lookback_days, "month_count": 0}

    trajectory_days = trajectory_day.summarize_days(start=start_dt, end=end_dt)
    summaries = trajectory_period.summarize_months(trajectory_days)
    return (
        {
            month: build_trajectory_summary(summary)
            for month, summary in summaries.items()
        },
        {
            "lookback_days": lookback_days,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "month_count": len(summaries),
        },
    )


def build_trajectory_summary(summary: trajectory_period.TrajectoryPeriodSummary) -> LifeMonthTrajectorySummary:
    return LifeMonthTrajectorySummary(
        start_date=summary.start_date,
        end_date=summary.end_date,
        days=summary.total_days,
        active_hours=round(summary.active_seconds / 3600.0, 2),
        recovery_hours=round(summary.recovery_seconds / 3600.0, 2),
        chain_count=summary.chain_count,
        signal_count=summary.signal_count,
        command_count=summary.command_count,
        transcript_count=summary.transcript_count,
        commit_count=summary.commit_count,
        dominant_modes=[(mode, round(seconds / 3600.0, 2)) for mode, seconds in summary.dominant_modes],
        dominant_projects=[(project, round(seconds / 3600.0, 2)) for project, seconds in summary.dominant_projects],
        source_counts=dict(summary.source_counts),
        coverage=dict(summary.coverage),
        highlights=list(summary.highlights),
    )


def render_markdown(payload: Mapping[str, object]) -> str:
    generated_at = payload.get("generated_at", "<unknown>")
    months = payload.get("months") or {}
    if not isinstance(months, Mapping):
        months = {}
    range_info = payload.get("range") or {}
    start_month = range_info.get("start_month", "<unknown>") if isinstance(range_info, Mapping) else "<unknown>"
    end_month = range_info.get("end_month", "<unknown>") if isinstance(range_info, Mapping) else "<unknown>"
    output_path = payload.get("output_path")

    lines: list[str] = []
    lines.append(f"# Life timeline auto-summary ({start_month} → {end_month})")
    lines.append("")
    lines.append(f"Generated: `{generated_at}`")
    if output_path:
        lines.append(f"Backing JSON: `{output_path}`")
    lines.append("")
    for month in sorted(months.keys()):
        m = months[month]
        if not isinstance(m, Mapping):
            continue
        out = m.get("output") or {}
        work = m.get("work") or {}
        intake = m.get("intake") or {}
        mail = m.get("mail") or {}
        location = m.get("location") or {}
        money = m.get("money") or {}
        health = m.get("health") or {}
        trajectory = m.get("trajectory") or {}
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
        lines.append(f"- Mail: Gmail messages {mail.get('gmail_messages', 0)}.")
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
        if trajectory.get("days"):
            lines.append(
                "- Trajectory: "
                f"{trajectory.get('active_hours', 0)}h active / {trajectory.get('recovery_hours', 0)}h recovery across "
                f"{trajectory.get('days', 0)} day(s), {trajectory.get('chain_count', 0)} chains, {trajectory.get('signal_count', 0)} signals."
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

        if trajectory.get("days"):
            lines.append("**Trajectory (recent window)**")
            lines.append("")
            lines.append(f"- Dominant modes: {_render_counter(trajectory.get('dominant_modes') or [])}")
            lines.append(f"- Dominant projects: {_render_counter(trajectory.get('dominant_projects') or [])}")
            lines.append(f"- Highlights: {', '.join(trajectory.get('highlights') or []) or 'n/a'}")
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


def _render_counter(counter: Sequence[Sequence[object]], limit: int = 12) -> str:
    items = []
    for key, value in counter[:limit]:
        items.append(f"{key} {value}")
    return ", ".join(items)


def _month_start(month_key: str, tzinfo) -> datetime:
    year, month = (int(part) for part in month_key.split("-", 1))
    return datetime(year, month, 1, tzinfo=tzinfo)


def _month_after(month_key: str, tzinfo) -> datetime:
    year, month = (int(part) for part in month_key.split("-", 1))
    if month == 12:
        return datetime(year + 1, 1, 1, tzinfo=tzinfo)
    return datetime(year, month + 1, 1, tzinfo=tzinfo)
