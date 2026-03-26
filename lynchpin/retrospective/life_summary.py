from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from ..context.reports import build_period_report

RECENT_CONTEXT_LOOKBACK_DAYS = 62


@dataclass(frozen=True)
class LifeMonthWorkSummary:
    git_commits: int
    git_top_repos: list[tuple[str, int]]
    chat_session_count: int = 0
    chat_work_events: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.chat_work_events is None:
            object.__setattr__(self, "chat_work_events", {})

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
class LifeMonthContextSummary:
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
    dominant_topics: list[tuple[str, float]]
    source_counts: dict[str, int]
    coverage: dict[str, object]
    highlights: list[str]
    chat_session_count: int = 0
    chat_work_events: dict[str, int] = None  # type: ignore[assignment]
    chat_cost_usd: float = 0.0
    episode_count: int = 0
    episode_labels: list[str] = None  # type: ignore[assignment]
    anomaly_count: int = 0
    anomaly_kinds: list[str] = None  # type: ignore[assignment]
    top_repos: list[tuple[str, int]] = None  # type: ignore[assignment]
    top_paths: list[tuple[str, int]] = None  # type: ignore[assignment]
    top_session_titles: list[tuple[str, int]] = None  # type: ignore[assignment]
    avg_fragmentation: Optional[float] = None
    evidence_bundle: str | None = None

    def __post_init__(self):
        if self.chat_work_events is None:
            object.__setattr__(self, "chat_work_events", {})
        if self.episode_labels is None:
            object.__setattr__(self, "episode_labels", [])
        if self.anomaly_kinds is None:
            object.__setattr__(self, "anomaly_kinds", [])
        if self.top_repos is None:
            object.__setattr__(self, "top_repos", [])
        if self.top_paths is None:
            object.__setattr__(self, "top_paths", [])
        if self.top_session_titles is None:
            object.__setattr__(self, "top_session_titles", [])

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
    context: Optional[LifeMonthContextSummary] = None
    narrative: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "output": self.output.to_dict(),
            "work": self.work.to_dict(),
            "intake": self.intake.to_dict(),
            "mail": self.mail.to_dict(),
            "location": self.location.to_dict(),
            "money": self.money.to_dict(),
            "health": self.health.to_dict(),
            "context": self.context.to_dict() if self.context is not None else {},
            "notes": self.notes.to_dict(),
            "narrative": self.narrative,
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


def build_recent_context_summaries(
    months: Sequence[str],
    *,
    lookback_days: int = RECENT_CONTEXT_LOOKBACK_DAYS,
    now: Optional[datetime] = None,
) -> tuple[dict[str, LifeMonthContextSummary], dict[str, object]]:
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

    reports = {
        month: build_period_report("month", month, output_root=None, write_files=False)
        for month in sorted(target_months)
    }

    return (
        {month: _report_to_context_summary(report) for month, report in reports.items()},
        {
            "lookback_days": lookback_days,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "month_count": len(reports),
            "source": "context-reports",
        },
    )


def _report_to_context_summary(report: Any) -> LifeMonthContextSummary:
    payload = report.payload if isinstance(getattr(report, "payload", None), Mapping) else {}
    period = payload.get("period") if isinstance(payload, Mapping) else {}
    summary = payload.get("summary") if isinstance(payload, Mapping) else {}
    evidence = summary.get("evidence") if isinstance(summary, Mapping) else {}
    delivery = summary.get("delivery") if isinstance(summary, Mapping) else {}
    focus = summary.get("focus") if isinstance(summary, Mapping) else {}
    chat = summary.get("chat") if isinstance(summary, Mapping) else {}
    git = summary.get("git") if isinstance(summary, Mapping) else {}
    circadian = summary.get("circadian") if isinstance(summary, Mapping) else {}
    patterns = summary.get("patterns") if isinstance(summary, Mapping) else {}
    if not isinstance(patterns, Mapping):
        patterns = {}
    query_rows = evidence.get("query_rows") if isinstance(evidence, Mapping) else {}
    if not isinstance(query_rows, Mapping):
        query_rows = {}
    highlights: list[str] = []
    if delivery.get("top_repos"):
        highlights.append(f"Repos: {_render_counter(delivery.get('top_repos') or [], limit=3)}")
    if git.get("top_paths"):
        highlights.append(f"Paths: {_render_counter(git.get('top_paths') or [], limit=3)}")
    if chat.get("top_session_titles"):
        highlights.append(f"Sessions: {_render_counter(chat.get('top_session_titles') or [], limit=3)}")
    if patterns.get("episode_count"):
        labels = patterns.get("episode_labels") or []
        highlights.append(f"Episodes: {int(patterns.get('episode_count') or 0)} ({', '.join(labels[:3]) or 'n/a'})")
    if patterns.get("anomaly_count"):
        kinds = patterns.get("anomaly_kinds") or []
        highlights.append(f"Anomalies: {int(patterns.get('anomaly_count') or 0)} ({', '.join(kinds[:3]) or 'n/a'})")
    return LifeMonthContextSummary(
        start_date=str(period.get("start") or ""),
        end_date=str(period.get("end") or ""),
        days=int(evidence.get("days_with_evidence") or evidence.get("period_days") or 0),
        active_hours=float(delivery.get("active_hours") or 0.0),
        recovery_hours=round(float(circadian.get("recovery_minutes_total") or 0.0) / 60.0, 2),
        chain_count=int(query_rows.get("focus_loops") or 0),
        signal_count=int(query_rows.get("focus_spans") or 0),
        command_count=int(delivery.get("command_count") or 0),
        transcript_count=int(query_rows.get("polylogue_sessions") or 0),
        commit_count=int(delivery.get("total_commits") or 0),
        dominant_modes=_counter_pairs(focus.get("top_modes") or circadian.get("dominant_modes") or [], divisor=60.0),
        dominant_projects=_counter_pairs(focus.get("top_projects") or circadian.get("dominant_projects") or [], divisor=60.0),
        dominant_topics=_counter_pairs(chat.get("work_kinds") or [], divisor=1.0),
        source_counts={str(key): int(value) for key, value in query_rows.items()},
        coverage={
            surface: {
                "present": surface in (evidence.get("surfaces_present") or []),
                "rows": int(query_rows.get(surface) or 0),
            }
            for surface in sorted(query_rows)
        },
        highlights=highlights,
        chat_session_count=int(delivery.get("chat_sessions") or 0),
        chat_work_events=_counter_mapping(chat.get("work_kinds") or []),
        chat_cost_usd=float(chat.get("total_cost_usd") or 0.0),
        episode_count=int(patterns.get("episode_count") or 0),
        episode_labels=[str(label) for label in (patterns.get("episode_labels") or [])],
        anomaly_count=int(patterns.get("anomaly_count") or 0),
        anomaly_kinds=[str(kind) for kind in (patterns.get("anomaly_kinds") or [])],
        top_repos=_counter_pairs(delivery.get("top_repos") or [], divisor=1.0),
        top_paths=_counter_pairs(git.get("top_paths") or [], divisor=1.0),
        top_session_titles=_counter_pairs(chat.get("top_session_titles") or [], divisor=1.0),
        avg_fragmentation=float(focus.get("avg_fragmentation")) if focus.get("avg_fragmentation") is not None else None,
        evidence_bundle=payload.get("bundle_ref"),
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
        context = m.get("context") or {}
        notes = m.get("notes") or {}

        lines.append(f"## {month}")
        lines.append("")
        narrative = m.get("narrative")
        if narrative:
            lines.append(f"> {narrative}")
            lines.append("")
        lines.append("**Snapshot**")
        lines.append("")
        lines.append(
            "- Output: "
            f"Reddit comments {out.get('reddit_comments', 0)}, posts {out.get('reddit_posts', 0)}, messages {out.get('reddit_messages', 0)}; "
            f"Wykop link-comments {out.get('wykop_link_comments', 0)}, entries {out.get('wykop_entries', 0)}, entry-comments {out.get('wykop_entry_comments', 0)}."
        )
        chat_sessions = work.get("chat_session_count", 0)
        chat_we = work.get("chat_work_events") or {}
        if chat_sessions:
            lines.append(
                f"- Work: git commits {work.get('git_commits', 0)}; "
                f"chat sessions {chat_sessions}, work events: {_render_counter(list(chat_we.items()))}."
            )
        else:
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
        if context.get("days"):
            context_line = (
                f"- Context: "
                f"{context.get('active_hours', 0)}h active / {context.get('recovery_hours', 0)}h recovery across "
                f"{context.get('days', 0)} day(s), {context.get('chain_count', 0)} focus loops, {context.get('signal_count', 0)} focus spans."
            )
            context_chat = context.get("chat_session_count", 0)
            if context_chat:
                context_line += f" Chat sessions: {context_chat}"
                chat_cost = float(context.get("chat_cost_usd") or 0.0)
                if chat_cost > 0:
                    context_line += f", cost: ${chat_cost:.2f}"
                context_line += "."
            if context.get("episode_count"):
                context_line += f" Episodes: {context.get('episode_count')}."
            if context.get("anomaly_count"):
                context_line += f" Anomalies: {context.get('anomaly_count')}."
            bundle_ref = context.get("evidence_bundle")
            if bundle_ref:
                context_line += f" Evidence: `{bundle_ref}`."
            lines.append(context_line)
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

        if context.get("days"):
            lines.append("**Recent Context**")
            lines.append("")
            lines.append(f"- Dominant modes: {_render_counter(context.get('dominant_modes') or [])}")
            lines.append(f"- Dominant projects: {_render_counter(context.get('dominant_projects') or [])}")
            if context.get("top_repos"):
                lines.append(f"- Top repos: {_render_counter(context.get('top_repos') or [])}")
            if context.get("top_paths"):
                lines.append(f"- Top paths: {_render_counter(context.get('top_paths') or [])}")
            if context.get("top_session_titles"):
                lines.append(f"- Session titles: {_render_counter(context.get('top_session_titles') or [])}")
            if context.get("avg_fragmentation") is not None:
                lines.append(f"- Avg fragmentation: {context.get('avg_fragmentation')}")
            if context.get("episode_count"):
                lines.append(
                    f"- Episodes: {context.get('episode_count')} ({', '.join(context.get('episode_labels') or []) or 'n/a'})"
                )
            if context.get("anomaly_count"):
                lines.append(
                    f"- Anomalies: {context.get('anomaly_count')} ({', '.join(context.get('anomaly_kinds') or []) or 'n/a'})"
                )
            lines.append(f"- Highlights: {', '.join(context.get('highlights') or []) or 'n/a'}")
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


def _counter_pairs(counter: Sequence[Sequence[object]], *, divisor: float) -> list[tuple[str, float | int]]:
    pairs: list[tuple[str, float | int]] = []
    for item in counter:
        if len(item) < 2:
            continue
        label = str(item[0])
        value = float(item[1] or 0.0)
        if divisor == 1.0:
            pairs.append((label, int(value)))
        else:
            pairs.append((label, round(value / divisor, 2)))
    return pairs


def _counter_mapping(counter: Sequence[Sequence[object]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for item in counter:
        if len(item) < 2:
            continue
        mapping[str(item[0])] = int(item[1] or 0)
    return mapping
